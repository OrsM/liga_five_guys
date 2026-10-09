
from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, NamedTuple

from ffcore.parse import Rows, text
from ffcore.startprob import NEUTRAL_START, StartOdds
from ffcore.names import Name, row_key
from ffcore.rules import minutes_played, slot
from stats import shrink

from ffcore.crosswalk import Crosswalk

__all__ = ["JornadaPoints", "Rating", "Rates", "Scored", "Scorer", "Tally",
           "fit_promoted_discount", "per_jornada_current", "points_by_jornada", "totals"]


SHRINK_K = 4.0

PROMOTED_DISCOUNT = 0.70

PROMOTED_DISCOUNT_K = 50.0

class Scored(NamedTuple):
    """A player's points in a jornada as the points table first showed them."""
    key: str
    jornada: int
    pts: float
    games: float
    at: str


# What each player scored in each jornada: (key, jornada) to points.
JornadaPoints = dict[tuple[str, int], float]


def points_by_jornada(played: Iterable[Scored]) -> JornadaPoints:
    """Each player's points in each jornada, summed once."""
    out: JornadaPoints = {}
    for s in played:
        out[s.key, s.jornada] = out.get((s.key, s.jornada), 0.0) + s.pts
    return out


# A player's season so far, by key: {"pts": points, "pj": matches played}.
Tally = dict[str, float]


def position_priors(market: Rows, history: Mapping[str, Tally]
                    ) -> tuple[dict[str, float], float]:
    samples: dict[str, list[float]] = {}
    for r in market:
        h = history.get(row_key(r))
        pos = slot(r.get("position"))
        if h and pos and h["pj"] >= 10:
            samples.setdefault(pos, []).append(h["pts"] / h["pj"])
    priors = {k: statistics.median(v) for k, v in samples.items() if v}
    flat = [p for v in samples.values() for p in v]
    return priors, (statistics.median(flat) if flat else 0.0)


def detect_promoted(market: Rows, history: Mapping[str, Tally]) -> set[str]:
    per_club: dict[str, list[int]] = {}
    for r in market:
        h = history.get(row_key(r))
        tally = per_club.setdefault(r.get("club") or "?", [0, 0])
        tally[0] += 1
        tally[1] += 1 if h and h["pj"] > 0 else 0
    return {c for c, (n, k) in per_club.items() if n >= 10 and k / n < 0.15}


def fit_promoted_discount(market: Rows, history: Mapping[str, Tally],
                          played: list[Scored]) -> float:
    promoted = detect_promoted(market, history)
    priors = position_priors(market, history)[0]
    prior_of: dict[str, float | None] = {row_key(r): priors.get(slot(r.get("position")))
                for r in market if r.get("club") in promoted}
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


def per_jornada_current(starters_rows: Rows, played: list[Scored],
                        jornada_of_match: Mapping[str, int], xw: Crosswalk
                        ) -> dict[str, dict[int, tuple[float, float]]]:
    minutes_by_jor: dict[str, dict[int, float]] = {}
    seen: set[tuple[str, str]] = set()
    for r in starters_rows:
        slug = text(r, "player_slug")
        mid = text(r, "match_id")
        jor = jornada_of_match.get(mid)
        if not slug or jor is None or r.get("role") not in ("starter", "sub"):
            continue
        key = xw.key_of(r)
        if not key:
            continue
        dedup = (mid, key)
        if dedup in seen:
            continue
        seen.add(dedup)
        by_j = minutes_by_jor.setdefault(key, {})
        by_j[jor] = by_j.get(jor, 0.0) + minutes_played(r.get("role") or "",
                                                         r.get("minute"))

    points_by_jor: dict[str, dict[int, float]] = {}
    for (k, j), pts in points_by_jornada(played).items():
        points_by_jor.setdefault(k, {})[j] = pts

    out: dict[str, dict[int, tuple[float, float]]] = {}
    for pk, points_jd in points_by_jor.items():
        minutes_jd = minutes_by_jor.get(pk, {})
        jors = set(points_jd) | set(minutes_jd)
        out[pk] = {j: (points_jd.get(j, 0.0), minutes_jd.get(j, 0.0))
                   for j in jors}
    return out


def totals(per_jornada: dict[int, tuple[float, float]]
           ) -> tuple[float, float, float]:
    """This season's points, appearances and matchday squads."""
    return (sum(p for p, _m in per_jornada.values()),
            float(sum(1 for _p, m in per_jornada.values() if m > 0)),
            float(len(per_jornada)))


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
    position's prior (discounted for promoted clubs), then this season's
    matches; and, from StartOdds, his chance of starting."""

    def __init__(self, market: Rows, starts: StartOdds,
                 last_season: Mapping[str, Tally] | None = None, shrink_k: float = SHRINK_K,
                 current: Mapping[str, Tally] | None = None,
                 promoted_discount: float = PROMOTED_DISCOUNT) -> None:
        self.market = market
        self.starts = starts
        self.last_season = last_season or {}
        self.shrink_k = shrink_k
        self.promoted_discount = promoted_discount
        self.current = current or {}
        self.lookup: dict[str, Mapping[str, Any]] = {row_key(r): r for r in market
                                        if r.get("name")}
        self.promoted = detect_promoted(self.market, self.last_season)
        self.priors, self.global_prior = position_priors(self.market,
                                                          self.last_season)

    def rate(self, rec: Mapping[str, Any]) -> Rating:
        """Points per match: last season pulled toward his position's typical
        rate (or that rate, discounted for a promoted club); then this
        season pulled toward that."""
        key = row_key(rec)
        prior = self.priors.get(slot(rec.get("position")),
                                self.global_prior)
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
        key = row_key(rec)
        return Rates(key, slot(rec.get("position")),
                     self.rate(rec).ppm, self.starts.picked(key, True),
                     self.starts.picked(key, False), self.rate(rec).pj)


def _selftest() -> None:
    from ffcore.names import AppId, PlayerKey  # noqa: F401
    from ffcore.tidy import typed

    K = SHRINK_K

    row = dict(typed("market", [{"position": "defensa", "team": "Mid", "club": "Mid",
                                 "value": "10.00M"}])[0])

    from ffcore.crosswalk import Crosswalk, Player

    market = [dict(row, name=n) for n in
              ["p%d" % i for i in range(10)] + ["Sub", "Newbie"]]
    xw = Crosswalk({Name(n).key: Player(PlayerKey(Name(n).key), n)
                    for n in [r["name"] for r in market] + ["Attacker"]})
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}
    xi = typed("lineups", [{"player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]])

    sc = Scorer(market, StartOdds(xi, xw), hist)
    prior = sc.priors["DEF"]
    assert 3.0 < prior < 3.1, prior

    thin = sc.rate(dict(row, name="Sub"))
    assert abs(thin.ppm - (20.0 + K * prior) / (4.0 + K)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(dict(row, name="Newbie")).assumed

    full = sc.rate(dict(row, name="p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, StartOdds(xi, xw), hist, current=cur)
    blended = sc2.rate(dict(row, name="p0"))
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
    sc3 = Scorer(promo_market, StartOdds([], xw), promo_hist)
    assert sc3.promoted == {"Rise"}
    newbie = sc3.rate(dict(row, name="q5", team="Rise", club="Rise"))
    assert newbie.assumed and abs(newbie.ppm - sc3.priors["DEF"]
                                  * PROMOTED_DISCOUNT) < 1e-9, newbie
    lower = Scorer(promo_market, StartOdds([], xw), promo_hist, promoted_discount=0.5)
    assert lower.rate(dict(row, name="q5", team="Rise", club="Rise")).ppm < newbie.ppm

    assert fit_promoted_discount(promo_market, promo_hist, []) \
        == PROMOTED_DISCOUNT
    at_discount = [Scored("q%d" % (i % 10), 1, round(
        sc3.priors["DEF"] * PROMOTED_DISCOUNT, 1), 1.0, "t") for i in range(80)]
    assert abs(fit_promoted_discount(promo_market, promo_hist, at_discount)
               - PROMOTED_DISCOUNT) < 0.01
    at_prior = [r._replace(pts=round(sc3.priors["DEF"], 1))
                for r in at_discount * 5]
    assert fit_promoted_discount(promo_market, promo_hist, at_prior) \
        > PROMOTED_DISCOUNT + 0.15

    assert Scorer(market, StartOdds(xi, xw), hist, current={}).rate(dict(row, name="p0")) == full
    assert Scorer(market, StartOdds(xi, xw), hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(dict(row, name="p0")) \
        == full

    dbt = typed("lineups", [{"player_name": "p0", "start_pct": "100", "status": "doubt"}])
    r0 = Scorer(market, StartOdds(dbt, xw), hist).rates(dict(row, name="p0"))
    assert (r0.key, r0.slot, r0.p_now, r0.p_rest) == ("p0", "DEF", 0.15, 0.15), \
        "flagged, no record, never listed fit: his 100% is not a selection signal"
    assert abs(r0.ppm - full.ppm) < 1e-9 and r0.pj == full.pj


    benched = Scorer(market, StartOdds(xi, xw, history={"p0": (0.0, 6.0)}), hist,
                     current={"p0": {"pts": 30.0, "pj": 3.0}}).rates(dict(row, name="p0"))
    assert abs(benched.p_now - K / (K + 6)) < 1e-9, benched
    susp = typed("lineups", [{"player_name": "p0", "start_pct": "0", "status": "suspended"}])
    back = Scorer(market, StartOdds(susp, xw, history={"p0": (1.8, 2.0)}), hist,
                  current={"p0": {"pts": 30.0, "pj": 2.0}}).rates(dict(row, name="p0"))
    assert abs(back.p_rest - (K * NEUTRAL_START / 100 + 1.8) / (K + 2)) < 1e-9, back
    assert back.p_now == back.p_rest, "the suspension is the fit factor's, not selection's"

    from ffcore.crosswalk import Crosswalk, Player

    xw2 = Crosswalk({
        "antonio blanco": Player(PlayerKey("antonio blanco"), "Antonio Blanco",
                                 ff_slug="blanco", app_id=AppId("1")),
        "came on": Player(PlayerKey("came on"), "Came On", ff_slug="came-on"),
        "unused sub": Player(PlayerKey("unused sub"), "Unused Sub", ff_slug="unused"),
    })
    jornada_map = {"m1": 1, "m2": 2}
    starters_rows = typed("starters", [
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "", "match_id": "m1"},
        {"player_name": "Came On", "player_slug": "came-on",
         "role": "sub", "minute": "70", "match_id": "m1"},
        {"player_name": "Unused Sub", "player_slug": "unused",
         "role": "sub", "minute": "", "match_id": "m1"},
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "45", "match_id": "m2"},
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "coach", "minute": "", "match_id": "m3"},
        {"player_name": "Nobody", "player_slug": "", "role": "starter",
         "minute": "", "match_id": "m1"},
        *({"player_name": "Blanco", "player_slug": "blanco",
           "role": "starter", "minute": "", "match_id": "m1"}
          for _ in range(56)),
    ])
    played = [Scored("antonio blanco", 1, 8.0, 1.0, "t1"),
              Scored("antonio blanco", 2, 5.0, 1.0, "t2")]
    by_key = per_jornada_current(starters_rows, played,
                                  jornada_map, xw2)
    assert by_key["antonio blanco"] == {1: (8.0, 90.0), 2: (5.0, 45.0)}, \
        by_key["antonio blanco"]
    assert "came on" not in by_key, by_key
    assert "unused sub" not in by_key, by_key
    assert per_jornada_current([], [], {}, xw2) == {}

    assert totals(by_key["antonio blanco"]) == (13.0, 2.0, 2.0)
    assert totals({}) == (0.0, 0.0, 0.0)

    print("ffcore.score self-test OK")


if __name__ == "__main__":
    _selftest()
