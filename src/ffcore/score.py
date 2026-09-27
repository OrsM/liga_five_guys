
from __future__ import annotations

import statistics
from typing import NamedTuple

from ffcore.parse import money, pct100, ratio, text
from ffcore.lineupweight import line_rows
from ffcore.startprob import NEUTRAL_START, Calibration, calibrate, outcomes
from ffcore.text import norm
from ffcore.tidy import (current, minutes_played, row_key)

__all__ = ["SLOT", "SLOT_LABEL", "SLOT_MIN", "MAX_SLOT", "FREE_FORMATIONS",
           "SHAPES", "starters_per_slot", "Rating", "Scorer", "pick_xi",
           "squad_pool", "replacement", "vor", "build"]

SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}
SLOT_LABEL = {"POR": "portero", "DEF": "defensa", "MED": "mediocampista",
              "DEL": "delantero"}
SLOT_MIN = {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}
MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}

FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]

SHRINK_K = 8.0
DOUBT_FACTOR = 0.5

OUT_STATUSES = frozenset({"injured", "suspended", "unavailable"})
PROMOTED_DISCOUNT = 0.70

PROMOTED_DISCOUNT_K = 50.0


def position_priors(market: list[dict], history: dict
                    ) -> tuple[dict[str, float], float]:
    samples: dict[str, list[float]] = {}
    for r in market:
        h = history.get(row_key(r))
        slot = SLOT.get((r.get("position") or "").lower())
        if h and slot and h["pj"] >= 10:
            samples.setdefault(slot, []).append(h["pts"] / h["pj"])
    priors = {k: statistics.median(v) for k, v in samples.items() if v}
    flat = [p for v in samples.values() for p in v]
    return priors, (statistics.median(flat) if flat else 0.0)


def detect_promoted(market: list[dict], history: dict) -> set[str]:
    per_club: dict[str, list[int]] = {}
    for r in market:
        h = history.get(row_key(r))
        tally = per_club.setdefault(r.get("club") or "?", [0, 0])
        tally[0] += 1
        tally[1] += 1 if h and h["pj"] > 0 else 0
    return {c for c, (n, k) in per_club.items() if n >= 10 and k / n < 0.15}


def fit_promoted_discount(market: list[dict], history: dict,
                          perjornada: list[dict]) -> float:
    promoted = detect_promoted(market, history)
    priors = position_priors(market, history)[0]
    prior_of = {row_key(r): priors.get(SLOT.get((r.get("position") or "").lower()))
                for r in market if r.get("club") in promoted}
    pts = expected = n = 0.0
    for r in perjornada:
        prior = prior_of.get(r.get("ff_id"))
        games = ratio(r.get("games_delta")) or 0.0
        if prior and games > 0:
            pts += ratio(r.get("points_delta")) or 0.0
            expected += prior * games
            n += games
    if expected <= 0:
        return PROMOTED_DISCOUNT
    k = PROMOTED_DISCOUNT_K
    return (k * PROMOTED_DISCOUNT + n * pts / expected) / (k + n)


def status_multiplier(status: str, factors: dict | None = None) -> float:
    if status in (factors or {}):
        return factors[status]
    if status in OUT_STATUSES:
        return 0.0
    if status == "doubt":
        return DOUBT_FACTOR
    return 1.0


DECAY_GRID = (1.0, 0.85, 0.7, 0.55, 0.4)


def _per90(r: dict, a: str, b: str, minutes: float) -> float:
    return (float(r.get(a) or 0) + float(r.get(b) or 0)) / minutes * 90


def _forwards(understat_rows, xw):
    for r in understat_rows:
        uid = text(r, "understat_id")
        key = xw.player(understat_id=uid) if uid else ""
        if key and "F" in (r.get("position") or ""):
            yield key, r, float(r.get("minutes") or 0)


def _linreg(xs, ys) -> tuple[float, float]:
    try:
        r = statistics.linear_regression(xs, ys)
        return r.slope, r.intercept
    except statistics.StatisticsError:
        return 0.0, statistics.fmean(ys)


def _xg_stickiness_boost(us25, us26) -> float:
    later = {r["understat_id"]: r for r in us26}
    pairs = []
    for r in us25:
        s = later.get(r["understat_id"])
        m25 = float(r.get("minutes") or 0)
        m26 = float((s or {}).get("minutes") or 0)
        if s and m25 >= 450 and m26 >= 30:
            pairs.append((_per90(r, "goals", "assists", m25),
                          _per90(s, "goals", "assists", m26),
                          _per90(r, "xg", "xa", m25), _per90(s, "xg", "xa", m26)))
    if len(pairs) < 30:
        return 1.0
    try:
        r_raw, r_xg = (max(0.02, min(0.9, statistics.correlation(
            [p[i] for p in pairs], [p[i + 1] for p in pairs]))) for i in (0, 2))
    except statistics.StatisticsError:
        return 1.0
    return max(0.5, min(3.0, ((1 - r_raw) / r_raw) / ((1 - r_xg) / r_xg)))


def xg_evidence(us25, us26, history: dict, xw) -> dict[str, tuple[float, float]]:
    xs, ys = [], []
    for key, r, mins in _forwards(us25, xw):
        h = history.get(key)
        if h and mins >= 450 and h["pj"] >= 10:
            xs.append(_per90(r, "xg", "xa", mins))
            ys.append(h["pts"] / h["pj"])
    if len(xs) < 10:
        return {}
    slope, intercept = _linreg(xs, ys)
    boost = _xg_stickiness_boost(us25, us26)
    return {key: (mins / 90 * boost, slope * _per90(r, "xg", "xa", mins) + intercept)
            for key, r, mins in _forwards(us26, xw) if mins > 0}


def _per_jornada_current(starters_rows, perjornada_rows, jornada_of_match,
                         xw) -> dict[str, dict[int, tuple[float, float]]]:
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
        by_j[jor] = by_j.get(jor, 0.0) + minutes_played(r.get("role"),
                                                         r.get("minute"))

    end_total: dict[str, dict[int, float]] = {}
    seen_at: dict[str, dict[int, str]] = {}
    for r in perjornada_rows:
        raw_jor = text(r, "jornada")
        if not raw_jor:
            continue
        jor = int(raw_jor)
        key = xw.key_of(r)
        if not key:
            continue
        total = ratio(r.get("points_total"))
        if total is None:
            continue
        end_total.setdefault(key, {})[jor] = total
        seen_at.setdefault(key, {})[jor] = (r.get("to_stamp")
                                            or r.get("from_stamp") or "")

    points_by_jor: dict[str, dict[int, float]] = {}
    for key, totals in end_total.items():
        order = sorted(totals, key=lambda j: seen_at[key].get(j, ""))
        prev = 0.0
        for jor in order:
            points_by_jor.setdefault(key, {})[jor] = totals[jor] - prev
            prev = totals[jor]

    out: dict[str, dict[int, tuple[float, float]]] = {}
    for key, points_jd in points_by_jor.items():
        minutes_jd = minutes_by_jor.get(key, {})
        jors = set(points_jd) | set(minutes_jd)
        out[key] = {j: (points_jd.get(j, 0.0), minutes_jd.get(j, 0.0))
                   for j in jors}
    return out


def _weighted(per_jornada: dict[int, tuple[float, float]], decay: float
              ) -> tuple[float, float, float, float]:
    latest = max(per_jornada, default=0)
    w = {j: decay ** (latest - j) for j in per_jornada}
    pts = sum(w[j] * p for j, (p, _m) in per_jornada.items())
    matches = sum(w[j] * m / 90.0 for j, (_p, m) in per_jornada.items())
    started = sum(w[j] * min(1.0, m / 90.0) for j, (_p, m) in per_jornada.items())
    n = sum(w.values())
    return pts, matches, (started / n if n else 0.0), n


def _walk_error(by_key: dict, decay: float) -> tuple[float, int]:
    se, n = 0.0, 0
    for jd in by_key.values():
        jors = sorted(jd)
        for i in range(1, len(jors)):
            actual_pts, actual_min = jd[jors[i]]
            wpts, wmatch, _s, _n = _weighted({j: jd[j] for j in jors[:i]}, decay)
            if actual_min <= 0 or wmatch <= 0:
                continue
            se += (wpts / wmatch - actual_pts / (actual_min / 90.0)) ** 2
            n += 1
    return (se / n, n) if n else (float("inf"), 0)


def _fit_decay(by_key: dict) -> float:
    errors = {d: _walk_error(by_key, d) for d in DECAY_GRID}
    return min(DECAY_GRID, key=lambda d: (errors[d][0], -d))


def build(market: list[dict], xi_rows: list[dict], now,
          shrink_k: float = SHRINK_K) -> "Scorer":
    from ffcore.fixture import difficulty_ratings, fit_home_edge, fixture_board
    from ffcore.tidy import (LINEUP_SOURCE, SEASON, history,
                             SECOND_SOURCE, clock_history, jornada_of_match, load_crosswalk,
                             load_perjornada,
                             read_csv)

    xw = load_crosswalk()
    files = sorted(SEASON.glob("points_*.csv"))
    last_season = {r["ff_id"]: {"pts": ratio(r.get("points")) or 0.0,
                            "pj": ratio(r.get("games")) or 0.0}
               for r in (read_csv(files[-1]) if files else ()) if r.get("ff_id")}
    perjornada = load_perjornada()
    by_key = _per_jornada_current(current("starters"), perjornada,
                                  jornada_of_match(), xw)
    decay = _fit_decay(by_key)
    us25, us26 = ([r for r in current("understat_players") if r["season"] == y]
                  for y in ("2025", "2026"))
    pos = {row_key(r): (r.get("position") or "").lower() for r in market}
    results = current("results_history")
    ratings = difficulty_ratings(
        market, results,
        fit_home_edge(results, current("matches")))
    second = history("lineups", SECOND_SOURCE)
    outs = outcomes(history("lineups", LINEUP_SOURCE) + second, current("starters"),
                    clock_history().round_locks, jornada_of_match(), xw)
    pos = {r["ff_id"]: r["position"] for r in history("market") if r.get("ff_id")}
    pts = {(r["ff_id"], int(r["jornada"])): float(r["points_delta"])
           for r in perjornada
           if r.get("games_delta") == "1" and r.get("jornada")}
    cal = calibrate(outs, line_rows(outs, pos, pts))
    return Scorer(
        market, xi_rows, last_season, shrink_k=shrink_k, xw=xw, cal=cal,
        second=second, ratings=ratings,
        board=fixture_board(ratings, current("fixtures"), now),
        current={k: dict(zip(("pts", "pj", "start_rate", "start_n"),
                             _weighted(jd, decay)))
                 for k, jd in by_key.items()},
        evidence={"xg": xg_evidence(us25, us26, last_season, xw)},
        promoted_discount=fit_promoted_discount(market, last_season, perjornada))


SHAPES = [{"POR": 1, "DEF": d, "MED": m, "DEL": f} for d, m, f in FREE_FORMATIONS]


class Rating(NamedTuple):
    ppm: float
    assumed: bool
    cur_pj: float = 0.0
    pj: float = 0.0


class Scored(NamedTuple):
    name: str
    key: str
    slot: str
    pos: str
    score: float
    flat: float
    ppm: float
    pct: float | None
    pct_used: float
    pct_rest: float
    on_page: bool
    status: str
    assumed: bool
    value: float
    fix: float = 1.0
    opp: str = ""
    home: bool = True
    cur_pj: float = 0.0
    pj: float = 0.0

    def as_row(self) -> dict:
        return dict(self._asdict())


class Scorer:

    def __init__(self, market: list[dict], xi: list[dict],
                 last_season: dict | None = None, shrink_k: float = SHRINK_K,
                 current: dict | None = None, board: dict | None = None,
                 cal=None, second=None, evidence: dict | None = None,
                 promoted_discount: float = PROMOTED_DISCOUNT, xw=None,
                 ratings=None):
        from ffcore.tidy import load_crosswalk

        self.market = market
        self.last_season = last_season or {}
        self.shrink_k = shrink_k
        self.promoted_discount = promoted_discount
        self.current = current or {}
        self.board = board or {}
        self.evidence = evidence or {}
        self.ratings = ratings
        self.lookup: dict[str, dict] = {row_key(r): r for r in market
                                        if r.get("name")}
        xw = xw if xw is not None else load_crosswalk()

        self.cal = cal or Calibration()
        self.second: dict[str, dict] = {}
        for r in second or []:
            k = xw.key_of(r) if xw else None
            if k:
                self.second[k] = r

        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        for r in xi or []:
            key = xw.key_of(r) if xw else None
            if not key:
                continue
            self.listed.add(key)
            p = pct100(r.get("start_pct"))
            if p is not None and p >= 0:
                self.start_pct[key] = max(self.start_pct.get(key, 0.0), p)
            if r.get("status") and r["status"] != "ok":
                self.status[key] = r["status"]

        self.promoted = detect_promoted(self.market, self.last_season)
        self.priors, self.global_prior = position_priors(self.market,
                                                          self.last_season)

    def rate(self, rec: dict) -> Rating:
        key = row_key(rec)
        prior = self.priors.get(SLOT.get((rec.get("position") or "").lower(), ""),
                                self.global_prior)
        k = self.shrink_k
        h = self.last_season.get(key)
        prior_pj = float(h["pj"]) if h and h["pj"] > 0 else 0.0
        if prior_pj:
            base = (h["pts"] + k * prior) / (prior_pj + k)
        elif (rec.get("club") or "") in self.promoted:
            base = prior * self.promoted_discount
        else:
            base = prior
        c = self.current.get(key)
        cur_pj = float(c["pj"]) if c and c["pj"] > 0 else 0.0
        terms = ([(k, base)] + ([(cur_pj, c["pts"] / cur_pj)] if cur_pj else [])
                 + [e for t in self.evidence.values()
                    if (e := t.get(key)) and e[0] > 0])
        return Rating(sum(w * m for w, m in terms) / sum(w for w, _ in terms),
                      not prior_pj and cur_pj < k, cur_pj, prior_pj + cur_pj)

    def score(self, rec: dict) -> Scored:
        key = row_key(rec)
        st = self.status.get(key, "")
        pct = self.start_pct.get(key)
        on_page = key in self.listed
        rating = self.rate(rec)

        raw = pct if pct is not None else (
            self.cal.neutral_start if on_page else self.cal.absent_start)
        pct_used = 100.0 * self.cal.p(raw, self.second.get(key))
        cur = self.current.get(key)
        start_n = cur.get("start_n", 0.0) if cur else 0.0
        if start_n > 0.0:
            k_s, k_l = self.shrink_k, self.cal.lineup_k or self.shrink_k
            pct_rest = (k_s * NEUTRAL_START + start_n * 100.0
                       * cur["start_rate"]) / (k_s + start_n)
            pct_used = (k_l * pct_used + start_n * 100.0 * cur["start_rate"]
                       ) / (k_l + start_n)
        else:
            pct_rest = pct_used
        m = self.board.get(rec.get("club"))
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        fix_factor = (m.def_factor if slot in ("POR", "DEF")
                     else m.atk_factor) if m else 1.0
        flat = rating.ppm * pct_used / 100.0
        score = flat * fix_factor
        mult = status_multiplier(st)
        score *= mult
        flat *= mult

        return Scored(
            name=rec.get("name", key), key=key,
            slot=slot,
            pos=(rec.get("position") or "").lower(),
            score=score, flat=flat, fix=fix_factor,
            opp=m.opponent if m else "", home=m.home if m else True,
            cur_pj=rating.cur_pj, pj=rating.pj,
            ppm=rating.ppm, pct=pct, pct_used=pct_used, pct_rest=pct_rest,
            on_page=on_page, status=st,
            assumed=rating.assumed,
            value=money(rec.get("value")) or 0.0,
        )

    def score_squad(self, names) -> tuple[list[Scored], list[str]]:
        out, missing = [], []
        for n in names:
            r = self.lookup.get(n)
            if r is None:
                missing.append(n)
            else:
                out.append(self.score(r))
        return out, missing


def squad_pool(scored) -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {}
    for p in scored:
        row = p.as_row() if isinstance(p, Scored) else p
        if row.get("slot"):
            pool.setdefault(row["slot"], []).append(row)
    for v in pool.values():
        v.sort(key=lambda p: p["score"], reverse=True)
    return pool


def starters_per_slot() -> dict[str, float]:
    shapes = FREE_FORMATIONS
    n = len(shapes)
    tot = {"POR": float(n), "DEF": 0.0, "MED": 0.0, "DEL": 0.0}
    for d, m, f in shapes:
        tot["DEF"] += d
        tot["MED"] += m
        tot["DEL"] += f
    return {k: v / n for k, v in tot.items()}


def replacement(pool: dict, squads: int) -> dict[str, float]:
    per = starters_per_slot()
    out = {}
    for slot, rows in pool.items():
        if not rows:
            continue
        rung = max(1, round(squads * per.get(slot, 0.0)))
        out[slot] = rows[min(rung, len(rows)) - 1]["score"]
    return out


def vor(row: dict, repl: dict) -> float:
    slot = row.get("slot")
    if not slot:
        return 0.0
    return row.get("score", 0.0) - repl.get(slot, 0.0)


def _xi_search(by_slot: dict[str, list], shapes, force=None):
    if force is None:
        prefix: dict[str, list[float]] = {}
        for slot, items in by_slot.items():
            acc, ps = 0.0, [0.0]
            for _, v in items:
                acc += v
                ps.append(acc)
            prefix[slot] = ps

        best = None
        for shape in shapes:
            picked, total, ok = [], 0.0, True
            for slot, n in shape.items():
                items = by_slot.get(slot, [])
                if len(items) < n:
                    ok = False
                    break
                picked += [it for it, _ in items[:n]]
                total += prefix[slot][n]
            if ok and (best is None or total > best[0]):
                best = (total, shape, picked)
        return best

    f_item, f_slot, f_val = force
    best = None
    for shape in shapes:
        if not f_slot or shape.get(f_slot, 0) < 1:
            continue
        picked, ok = [], True
        for slot, n in shape.items():
            items = by_slot.get(slot, [])
            if slot == f_slot:
                rest = [it for it in items if it[0] is not f_item][:n - 1]
                take = [(f_item, f_val)] + rest
            else:
                take = items[:n]
            if len(take) < n:
                ok = False
                break
            picked += take
        if not ok:
            continue
        total = sum(v for _, v in picked)
        if best is None or total > best[0]:
            best = (total, shape, [it for it, _ in picked])
    return best


def pick_xi(pool: dict, force: dict | None = None):
    by_slot = {slot: [(p, p["score"]) for p in rows]
              for slot, rows in pool.items()}
    f = (force, force["slot"], force["score"]) if force is not None else None
    got = _xi_search(by_slot, SHAPES, f)
    if got is None:
        return None
    total, shape, picked = got
    return total, (shape["DEF"], shape["MED"], shape["DEL"]), picked


def _selftest() -> None:
    assert status_multiplier("injured") == 0.0 and status_multiplier("doubt") == DOUBT_FACTOR
    assert status_multiplier("injured", {"injured": 0.5}) == 0.5
    assert status_multiplier("ok", {"injured": 0.5}) == 1.0
    from ffcore.fixture import Match

    row = {"position": "defensa", "team": "Mid", "club": "Mid",
           "value": "10.00M"}

    from ffcore.crosswalk import Crosswalk, Player

    market = [dict(row, name=n) for n in
              ["p%d" % i for i in range(10)] + ["Sub", "Newbie"]]
    xw = Crosswalk({norm(n): Player(norm(n), n)
                    for n in [r["name"] for r in market] + ["Attacker"]})
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}
    xi = [{"player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]]

    sc = Scorer(market, xi, hist, xw=xw)
    prior = sc.priors["DEF"]
    assert 3.0 < prior < 3.1, prior

    thin = sc.rate(dict(row, name="Sub"))
    assert abs(thin.ppm - (20.0 + 8 * prior) / (4.0 + 8)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(dict(row, name="Newbie")).assumed

    full = sc.rate(dict(row, name="p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, xi, hist, current=cur, xw=xw)
    blended = sc2.rate(dict(row, name="p0"))
    assert abs(blended.ppm - (30.0 + 8 * full.ppm) / (3.0 + 8)) < 1e-9
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
    sc3 = Scorer(promo_market, [], promo_hist, xw=xw)
    assert sc3.promoted == {"Rise"}
    newbie = sc3.rate(dict(row, name="q5", team="Rise", club="Rise"))
    assert newbie.assumed and abs(newbie.ppm - sc3.priors["DEF"]
                                  * PROMOTED_DISCOUNT) < 1e-9, newbie
    lower = Scorer(promo_market, [], promo_hist, promoted_discount=0.5, xw=xw)
    assert lower.rate(dict(row, name="q5", team="Rise", club="Rise")).ppm < newbie.ppm

    assert fit_promoted_discount(promo_market, promo_hist, []) \
        == PROMOTED_DISCOUNT
    at_discount = [{"ff_id": "q%d" % (i % 10), "games_delta": "1",
                    "points_delta": "%.1f" % (sc3.priors["DEF"]
                                              * PROMOTED_DISCOUNT)}
                   for i in range(80)]
    assert abs(fit_promoted_discount(promo_market, promo_hist, at_discount)
               - PROMOTED_DISCOUNT) < 0.01
    at_prior = [dict(r, points_delta="%.1f" % sc3.priors["DEF"])
                for r in at_discount * 5]
    assert fit_promoted_discount(promo_market, promo_hist, at_prior) \
        > PROMOTED_DISCOUNT + 0.15

    assert Scorer(market, xi, hist, current={}, xw=xw).rate(dict(row, name="p0")) == full
    assert Scorer(market, xi, hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}, xw=xw).rate(dict(row, name="p0")) \
        == full

    when = __import__("datetime").datetime.fromisoformat(
        "2026-08-20T19:00:00+00:00")
    easy = Match("Elche", True, when, atk_factor=1.30, def_factor=1.10)
    sc3 = Scorer(market, xi, hist, board={"Mid": easy}, xw=xw)
    s = sc3.score(dict(row, name="p0"))
    assert abs(s.flat - full.ppm) < 1e-9
    assert abs(s.score - full.ppm * 1.10) < 1e-9
    assert s.opp == "Elche" and s.home and s.fix == 1.10
    fwd = sc3.score(dict(row, name="p0", position="delantero"))
    assert abs(fwd.score - full.ppm * 1.30) < 1e-9, fwd
    assert fwd.fix == 1.30
    solo = Scorer(market, xi, hist, board={}, xw=xw).score(dict(row, name="p0"))
    assert solo.fix == 1.0 and solo.opp == "" and solo.score == solo.flat

    out = [{"player_name": "p0", "start_pct": "100", "status": "suspended"}]
    zero = Scorer(market, out, hist, board={"Mid": easy}, xw=xw).score(dict(row, name="p0"))
    assert zero.score == 0.0 and zero.flat == 0.0
    dbt = [{"player_name": "p0", "start_pct": "100", "status": "doubt"}]
    half = Scorer(market, dbt, hist, board={"Mid": easy}, xw=xw).score(dict(row, name="p0"))
    assert abs(half.flat - full.ppm * DOUBT_FACTOR) < 1e-9
    assert abs(half.score - full.ppm * 1.10 * DOUBT_FACTOR) < 1e-9

    assert "fix" in s.as_row() and "flat" in s.as_row()

    per = starters_per_slot()
    assert per == {"POR": 1.0, "DEF": 4.0, "MED": 4.0, "DEL": 2.0}, per
    assert abs(sum(per.values()) - 11.0) < 1e-9
    pool = {"POR": [{"score": s_} for s_ in (9.0, 8.0, 7.0, 6.0, 5.0, 4.0)],
           "DEL": [{"score": s_} for s_ in (9.0, 8.0)]}
    repl = replacement(pool, squads=5)
    assert repl["POR"] == 5.0, repl
    assert repl["DEL"] == 8.0, repl
    assert vor({"slot": "POR", "score": 9.0}, repl) == 4.0
    assert vor({"slot": "DEL", "score": 6.0}, repl) == -2.0
    assert vor({"slot": ""}, repl) == 0.0

    benched_cur = {"p0": {"pts": 30.0, "pj": 3.0,
                          "start_rate": 0.0, "start_n": 6.0}}
    sc4 = Scorer(market, xi, hist, current=benched_cur, board={"Mid": easy}, xw=xw)
    benched_s = sc4.score(dict(row, name="p0"))
    assert benched_s.pct_used < 100.0, benched_s.pct_used
    assert abs(benched_s.pct_used - 800.0 / 14.0) < 1e-9, benched_s.pct_used

    untouched = Scorer(market, xi, hist, current={}, board={"Mid": easy}
                       , xw=xw).score(dict(row, name="p0"))
    assert untouched.pct_used == 100.0, untouched.pct_used
    assert untouched.pct_rest == 100.0, untouched.pct_rest

    starter_cur = {"p0": {"pts": 30.0, "pj": 2.0,
                          "start_rate": 0.9, "start_n": 2.0}}
    susp = [{"player_name": "p0", "start_pct": "0", "status": "suspended"}]
    sc5 = Scorer(market, susp, hist, current=starter_cur, board={"Mid": easy}, xw=xw)
    susp_s = sc5.score(dict(row, name="p0"))
    assert abs(susp_s.pct_used - (8 * 0.0 + 2 * 90.0) / 10) < 1e-9, susp_s
    assert abs(susp_s.pct_rest - (8 * NEUTRAL_START + 2 * 90.0) / 10) < 1e-9, \
        susp_s
    assert susp_s.pct_rest > susp_s.pct_used + 25.0, susp_s

    from ffcore.crosswalk import Crosswalk, Player

    xw2 = Crosswalk({
        "antonio blanco": Player("antonio blanco", "Antonio Blanco",
                                 ff_slug="blanco", app_id="1"),
        "came on": Player("came on", "Came On", ff_slug="came-on"),
        "unused sub": Player("unused sub", "Unused Sub", ff_slug="unused"),
    })
    jornada_map = {"m1": 1, "m2": 2}
    starters_rows = [
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
    ]
    perjornada_rows = [
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "3", "points_total": "8", "jornada": "1"},
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "5", "points_total": "13", "jornada": "2"},
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "99", "points_total": "112", "jornada": ""},
    ]
    by_key = _per_jornada_current(starters_rows, perjornada_rows,
                                  jornada_map, xw2)
    assert by_key["antonio blanco"] == {1: (8.0, 90.0), 2: (5.0, 45.0)}, \
        by_key["antonio blanco"]
    assert "came on" not in by_key, by_key
    assert "unused sub" not in by_key, by_key
    assert _per_jornada_current([], [], {}, xw2) == {}

    corrected = _per_jornada_current(
        starters_rows,
        [{"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "8", "jornada": "1"},
         {"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "9", "jornada": "1"}],
        jornada_map, xw2)
    assert corrected["antonio blanco"] == {1: (9.0, 90.0), 2: (0.0, 45.0)}, \
        corrected

    assert _weighted(by_key["antonio blanco"], 1.0) == (13.0, 1.5, 0.75, 2.0)
    assert abs(_weighted(by_key["antonio blanco"], 0.5)[0] - 9.0) < 1e-9
    assert _weighted({}, 0.5) == (0.0, 0.0, 0.0, 0.0)

    assert _fit_decay({"a": {1: (4.0, 90.0)}, "b": {1: (2.0, 45.0)}}) == 1.0
    assert _fit_decay({}) == 1.0
    assert _fit_decay({p: {1: (1.0, 90.0), 2: (9.0, 90.0)} for p in "ab"}) == 1.0
    assert _fit_decay({p: {1: (1.0, 90.0), 2: (5.0, 90.0), 3: (9.0, 90.0)}
                       for p in ("p%d" % i for i in range(8))}) < 1.0

    slope, intercept = _linreg([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
    assert abs(slope - 2.0) < 1e-9 and abs(intercept) < 1e-9
    slope0, intercept0 = _linreg([1.0, 1.0, 1.0], [5.0, 5.0, 5.0])
    assert slope0 == 0.0 and abs(intercept0 - 5.0) < 1e-9

    xw_us = Crosswalk({**{"f%d" % i: Player("f%d" % i, "F%d" % i,
                                            understat_id=str(i))
                          for i in range(12)},
                       "d": Player("d", "D", understat_id="99")})
    us25 = [{"understat_id": str(i), "position": "F S", "minutes": "900",
             "xg": str(i / 10), "xa": "0"} for i in range(12)]
    us26 = [{"understat_id": "3", "position": "F S", "minutes": "180",
             "xg": "0.4", "xa": "0.2"},
            {"understat_id": "99", "position": "D", "minutes": "90",
             "xg": "1", "xa": "0"}]
    hist_us = {"f%d" % i: {"pts": 10.0 * (1 + i), "pj": 10.0}
               for i in range(12)}
    ev = xg_evidence(us25, us26, hist_us, xw_us)
    assert set(ev) == {"f3"}, ev
    assert abs(ev["f3"][0] - 2.0) < 1e-9 and abs(ev["f3"][1] - 31.0) < 1e-6, ev
    assert xg_evidence(us25[:9], us26, hist_us, xw_us) == {}
    assert _xg_stickiness_boost(us25, us26) == 1.0

    market_xg = [dict(row, name="Attacker", position="delantero")]
    hist_xg = {"attacker": {"pts": 100.0, "pj": 34.0}}
    xi_xg = [{"player_name": "Attacker", "start_pct": "100"}]
    fwd = dict(row, name="Attacker", position="delantero")
    plain = Scorer(market_xg, xi_xg, hist_xg, xw=xw).rate(fwd)
    expect = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    for label in ("xg", "other"):
        got = Scorer(market_xg, xi_xg, hist_xg, xw=xw,
                     evidence={label: {"attacker": (2.0, 10.0)}}).rate(fwd)
        assert abs(got.ppm - expect) < 1e-9, (label, got, expect)
        assert got.cur_pj == 0.0 and got.pj == 34.0, got
    assert Scorer(market_xg, xi_xg, hist_xg, xw=xw,
                  evidence={"xg": {}}).rate(fwd) == plain

    print("ffcore.score self-test OK")


if __name__ == "__main__":
    _selftest()
