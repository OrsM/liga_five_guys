"""
ffcore.score — the ranking index, and the legal-XI picker that consumes it.

Lifted out of report.py so rivals.py scores rival squads with the SAME
function. A comparison between your squad and theirs is meaningless if the
two sides were scored by two copies of the arithmetic that have drifted
apart, and copies always drift.

    score = shrunk points-per-match  x  fixture factor  x  P(start)

Points-per-match comes from data/season/points_*.csv. A raw average is
untrustworthy on few appearances, so it is pulled toward the median for that
position:

    shrunk = (total_points + K * prior) / (matches + K)      K = 8 matches

THIS SEASON, ONCE IT EXISTS, is a second stage of the same shrinkage — last
season's shrunk figure becomes the prior that this season's points are pulled
toward, with the same K:

    ppm = (points_now + K * shrunk_last_season) / (matches_now + K)

So one jornada moves a rating by about a ninth of the gap between the two, and
by the twentieth it is almost entirely this season. No new constant, and with
no current-season data it collapses EXACTLY to the line above — which is where
it sits today: futbolfantasy's points page reads "No se encontraron
resultados" until J1 finishes. This is the fix for the model's most concrete
error, which is not the formula but the input: last season's average cannot
know that a player changed club, aged, or lost his place.

THE FIXTURE FACTOR comes from ffcore.fixture and is the opponent the player
actually faces next, home or away. `score` carries it; `flat` is the same
arithmetic without it. Both are returned because they answer different
questions: you FIELD for one round, so the fixture belongs in that decision,
and you BUY for months, so it does not belong in that one. A bid sized on a
kind fixture is a bid for a fixture, not for a player.

The result is a RANKING INDEX, not a points forecast. Three things it cannot
know, each surfaced rather than hidden:

  * Promoted-side players have no top-flight record, so they fall back to the
    positional prior — the median top-flight starter, which flatters them.
    Their ratings are marked `assumed` and discounted. Promotion is detected
    from the data, not hardcoded, so it keeps working next season.
  * A player absent from the probable-XI page is not the same as one listed
    with no percentage. The first gets ABSENT_START, the second NEUTRAL_START.
  * Nothing here has been checked against reality yet. Log the inputs
    alongside every recommendation and score them once jornadas exist.

Scoring a rival's squad carries one extra caveat over scoring your own: you
know your roster exactly, while theirs comes from replaying the ledger, so
any name still unmatched in data/tidy is silently missing from their total.
Report the unmatched count next to the total or the comparison flatters you.
"""

from __future__ import annotations

import statistics
from typing import NamedTuple

from ffcore.parse import money, pct100, ratio
from ffcore.text import norm

__all__ = ["SLOT", "SLOT_LABEL", "SLOT_MIN", "MAX_SLOT", "THIN",
           "FREE_FORMATIONS", "PREMIUM_FORMATIONS", "formations",
           "Rating", "Scorer", "pick_xi", "squad_pool",
           "load_points", "build"]

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
# Most that can ever be on the pitch — anyone deeper than this in his position
# can never start under any legal formation.
MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}
# Below this you cannot absorb a single injury without a scramble.
THIN = {"POR": 2, "DEF": 4, "MED": 4, "DEL": 2}

# Confirmed against the app's formation picker.
FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]
# Premium subscription: these shapes, the captain boost and the coach slot.
PREMIUM_FORMATIONS = [(5, 2, 3), (4, 6, 0), (4, 2, 4), (3, 6, 1), (3, 3, 4)]

SHRINK_K = 8.0            # matches of prior weight
NEUTRAL_START = 60.0      # listed on the XI page but no percentage given
ABSENT_START = 15.0       # not on the XI page at all — not in the picture
DOUBT_FACTOR = 0.5

# Statuses that mean he cannot play at all, as opposed to might not. Scored
# at zero rather than shrunk: a suspended player is not a risk, he is an
# absence, and the XI picker has to see that difference.
OUT_STATUSES = frozenset({"injured", "suspended", "unavailable"})
PROMOTED_DISCOUNT = 0.70  # the LaLiga median overstates a promoted squad


def load_points() -> tuple[dict, str, dict, str]:
    """(prior, prior_label, current, current_label) from data/season/.

    Two files, not one. The newest points_*.csv is THIS season; the one before
    it is the prior the blend shrinks toward. Reading only the newest — which
    is what report.py and rivals.py each did, in their own copy of this
    function — was a bug waiting for the season to roll over: the moment
    points_2026-27.csv appeared, every rating would have been rebuilt from a
    one-jornada sample and last season would have vanished.

    With one file present it is the prior and there is no current season, which
    is today's state.
    """
    from ffcore.tidy import SEASON, read_csv

    files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []

    def read(path) -> dict:
        out: dict[str, dict] = {}
        for r in read_csv(path):
            rec = {"pts": ratio(r.get("points")) or 0.0,
                   "pj": ratio(r.get("games")) or 0.0}
            for key in (r.get("player_name"), r.get("player_name_full")):
                if key:
                    out.setdefault(norm(key), rec)
        return out

    def label(path) -> str:
        return path.stem.replace("points_", "")

    if not files:
        return {}, "", {}, ""
    if len(files) == 1:
        return read(files[0]), label(files[0]), {}, ""
    return (read(files[-2]), label(files[-2]),
            read(files[-1]), label(files[-1]))


def build(market: list[dict], xi_rows: list[dict], now,
          shrink_k: float = SHRINK_K) -> tuple:
    """(Scorer, labels) wired to every input the model has.

    ONE builder, because report.py and rivals.py must score with identical
    arithmetic — the whole reason this module was lifted out of report.py. They
    previously held a copy each of the points loader, and neither knew about
    the fixture board; a comparison between your squad and a rival's would
    have been between two different models.
    """
    from ffcore.fixture import fixture_board
    from ffcore.tidy import load_fixtures

    prior, prior_label, cur, cur_label = load_points()
    board = fixture_board(market, load_fixtures(), now)
    sc = Scorer(market, xi_rows, prior, shrink_k=shrink_k,
                current=cur, board=board)
    return sc, (prior_label, cur_label)


def formations(premium: bool = False) -> list[tuple]:
    """Legal shapes. Rivals may hold a premium subscription even if you
    don't, so this is a parameter rather than a module constant."""
    return FREE_FORMATIONS + (PREMIUM_FORMATIONS if premium else [])


class Rating(NamedTuple):
    ppm: float          # shrunk points per match
    why: str            # "412p/34j" or "assumed"
    assumed: bool       # no top-flight record — treat with suspicion
    cur_pj: float = 0.0  # matches of THIS season inside ppm, 0 = none yet


class Scored(NamedTuple):
    name: str
    key: str
    slot: str
    pos: str
    score: float           # includes the fixture — for FIELDING this round
    flat: float            # ignores the fixture — for BUYING, which is months
    ppm: float
    pct: float | None       # as published, None if unknown
    pct_used: float         # what the score actually used
    on_page: bool
    status: str
    note: str               # diagnosis / expected return, when published
    assumed: bool
    why: str
    value: float
    delta_1d: float
    delta_pct: float
    fix: float = 1.0        # fixture factor, 1.0 = neutral or unknown
    opp: str = ""           # who he faces next, "" if no fixture is known
    home: bool = True
    cur_pj: float = 0.0     # matches of this season behind ppm

    def as_row(self) -> dict:
        """pick_xi and the report renderers work in plain dicts."""
        return dict(self._asdict())


class Scorer:
    """Build once per run, then score any player from any squad.

        sc = Scorer(market_rows, xi_rows, history)
        rec = sc.score(market_row)

    `history` is {normalised name: {"pts": float, "pj": float}} — what
    report.load_history() already produces.
    """

    def __init__(self, market: list[dict], xi: list[dict],
                 history: dict | None = None, shrink_k: float = SHRINK_K,
                 current: dict | None = None, board: dict | None = None):
        self.market = market
        self.history = history or {}
        self.shrink_k = shrink_k
        # This season so far, same shape as `history`. Empty until a jornada
        # has been played, which is the state it must handle gracefully.
        self.current = current or {}
        # {team: ffcore.fixture.Match}. A team absent from it has no known
        # next fixture, and gets factor 1.0 with the reason printed — never a
        # silently average opponent.
        self.board = board or {}

        self.lookup: dict[str, dict] = {}
        for r in market:
            if r.get("slug"):
                self.lookup[r["slug"]] = r
            if r.get("name"):
                self.lookup[norm(r["name"])] = r

        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        self.notes: dict[str, str] = {}
        for r in xi or []:
            # Names are the only key both files share — the team pages expose
            # no player links, so slugs are unavailable there.
            key = norm(r.get("player_name")) or r.get("player_slug")
            if not key:
                continue
            self.listed.add(key)
            p = pct100(r.get("start_pct"))
            if p is not None and p >= 0:
                self.start_pct[key] = max(self.start_pct.get(key, 0.0), p)
            if r.get("status") and r["status"] != "ok":
                self.status[key] = r["status"]
                if r.get("note"):
                    self.notes[key] = r["note"]

        self.promoted = self._detect_promoted()
        self.priors, self.global_prior = self._priors()

    # -- calibration ---------------------------------------------------

    def _detect_promoted(self) -> set[str]:
        """A team with a full squad and essentially no top-flight record."""
        per_team: dict[str, list[int]] = {}
        for r in self.market:
            team = r.get("team") or "?"
            h = self.history.get(norm(r.get("name", "")))
            tally = per_team.setdefault(team, [0, 0])
            tally[0] += 1
            tally[1] += 1 if h and h["pj"] > 0 else 0
        return {t for t, (n, k) in per_team.items()
                if n >= 10 and k / n < 0.15}

    def _priors(self):
        samples: dict[str, list[float]] = {}
        for r in self.market:
            h = self.history.get(norm(r.get("name", "")))
            slot = SLOT.get((r.get("position") or "").lower())
            if h and slot and h["pj"] >= 10:
                samples.setdefault(slot, []).append(h["pts"] / h["pj"])
        priors = {k: statistics.median(v) for k, v in samples.items() if v}
        flat = [p for v in samples.values() for p in v]
        return priors, (statistics.median(flat) if flat else 0.0)

    # -- scoring -------------------------------------------------------

    def rate(self, rec: dict) -> Rating:
        key = norm(rec.get("name", ""))
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        prior = self.priors.get(slot, self.global_prior)
        k = self.shrink_k

        h = self.history.get(key)
        if h and h["pj"] > 0:
            base, why, assumed = ((h["pts"] + k * prior) / (h["pj"] + k),
                                  "%.0fp/%.0fj" % (h["pts"], h["pj"]), False)
        elif (rec.get("team") or "") in self.promoted:
            base, why, assumed = prior * PROMOTED_DISCOUNT, "assumed", True
        else:
            base, why, assumed = prior, "assumed", True

        # Second stage: this season shrunk toward last season's figure, same
        # K. With no matches played this is a no-op, which is exactly what it
        # must be — an empty points page must not reset anyone to the prior.
        c = self.current.get(key)
        if c and c["pj"] > 0:
            return Rating((c["pts"] + k * base) / (c["pj"] + k),
                          "%s + %.0fp/%.0fj now" % (why, c["pts"], c["pj"]),
                          assumed and c["pj"] < k, c["pj"])
        return Rating(base, why, assumed)

    def row_for(self, name):
        """Market row for a player name or slug, or None."""
        return self.lookup.get(name) or self.lookup.get(norm(name))

    def score(self, rec: dict) -> Scored:
        key = norm(rec.get("name", ""))
        st = self.status.get(key, "")
        pct = self.start_pct.get(key)
        on_page = key in self.listed
        rating = self.rate(rec)

        pct_used = pct if pct is not None else (
            NEUTRAL_START if on_page else ABSENT_START)
        m = self.board.get((rec.get("team") or "").strip())
        flat = rating.ppm * pct_used / 100.0
        score = flat * (m.factor if m else 1.0)
        if st in OUT_STATUSES:
            score = flat = 0.0
        elif st == "doubt":
            score *= DOUBT_FACTOR
            flat *= DOUBT_FACTOR

        return Scored(
            name=rec.get("name", key), key=key,
            slot=SLOT.get((rec.get("position") or "").lower(), ""),
            pos=(rec.get("position") or "").lower(),
            score=score, flat=flat, fix=m.factor if m else 1.0,
            opp=m.opponent if m else "", home=m.home if m else True,
            cur_pj=rating.cur_pj,
            ppm=rating.ppm, pct=pct, pct_used=pct_used,
            on_page=on_page, status=st, note=self.notes.get(key, ""),
            assumed=rating.assumed,
            why=rating.why,
            value=money(rec.get("value")) or 0.0,
            delta_1d=ratio(rec.get("delta_1d")) or 0.0,
            delta_pct=ratio(rec.get("delta_pct_1d")) or 0.0,
        )

    def score_squad(self, names) -> tuple[list[Scored], list[str]]:
        """Score a list of player names. Returns (scored, unresolved).

        Unresolved names are handed back rather than dropped: for a rival
        squad assembled by replaying the ledger, the count of names that
        didn't match is the honest error bar on their total.
        """
        out, missing = [], []
        for n in names:
            r = self.row_for(n)
            if r is None:
                missing.append(n)
            else:
                out.append(self.score(r))
        return out, missing


def squad_pool(scored) -> dict[str, list[dict]]:
    """Group scored players by slot, best first — the input to pick_xi."""
    pool: dict[str, list[dict]] = {}
    for p in scored:
        row = p.as_row() if isinstance(p, Scored) else p
        if row.get("slot"):
            pool.setdefault(row["slot"], []).append(row)
    for v in pool.values():
        v.sort(key=lambda p: p["score"], reverse=True)
    return pool


def pick_xi(pool: dict, force: dict | None = None, premium: bool = False):
    """Best legal XI by total score, or None if no legal shape fits.

    force pins one player into his slot. Exact, not heuristic: the only
    coupling between players is the per-slot count, so top-N per slot within
    each legal shape is optimal.

    Returns (total, (d, m, f), picked). A None return for a rival squad is
    itself a finding — it means they cannot field a legal XI today.
    """
    best = None
    for d, m, f in formations(premium):
        need = {"POR": 1, "DEF": d, "MED": m, "DEL": f}
        if force is not None:
            slot = force["slot"]
            if not slot or need.get(slot, 0) < 1:
                continue
        picked, ok = [], True
        for k, n in need.items():
            avail = pool.get(k, [])
            if force is not None and force["slot"] == k:
                rest = [p for p in avail if p is not force][:n - 1]
                take = [force] + rest
            else:
                take = avail[:n]
            if len(take) < n:
                ok = False
                break
            picked += take
        if not ok:
            continue
        tot = sum(p["score"] for p in picked)
        if best is None or tot > best[0]:
            best = (tot, (d, m, f), picked)
    return best


def _selftest() -> None:
    """The two stages of the blend, and the fixture that only fielding uses.

    score.py had no self-test: it was covered sideways through bid.py and
    report.py, which is coverage of the callers, not of the arithmetic. These
    are the cases the arithmetic owns.
    """
    from ffcore.fixture import Match

    def mk(name, pos="defensa", team="Mid", value="10.00M"):
        return {"name": name, "position": pos, "team": team, "value": value}

    # Ten priced defenders with a full record each, so the positional prior is
    # a real median rather than one player's average.
    market = [mk("p%d" % i) for i in range(10)] + [mk("Sub"), mk("Newbie")]
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}          # thin record: shrunk hard
    xi = [{"player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]]

    sc = Scorer(market, xi, hist)
    prior = sc.priors["DEF"]
    assert 3.0 < prior < 3.1, prior                  # median of 100..109 / 34

    # STAGE ONE, unchanged: a thin record is pulled toward the prior, and a
    # player with no record at all IS the prior, flagged assumed.
    thin = sc.rate(mk("Sub"))
    assert abs(thin.ppm - (20.0 + 8 * prior) / (4.0 + 8)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(mk("Newbie")).assumed

    # STAGE TWO: this season shrunk toward last season's shrunk figure.
    full = sc.rate(mk("p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, xi, hist, current=cur)
    blended = sc2.rate(mk("p0"))
    assert abs(blended.ppm - (30.0 + 8 * full.ppm) / (3.0 + 8)) < 1e-9
    assert blended.cur_pj == 3.0
    # 10 points a match beats his 3-ish, so the rating rises — but only part
    # of the way, because three matches is not a season.
    assert full.ppm < blended.ppm < 10.0
    assert "now" in blended.why and "3j" in blended.why

    # AN EMPTY CURRENT SEASON IS A NO-OP. This is today's live state: the
    # points page reads "No se encontraron resultados" until J1 finishes, and
    # that must not reset anybody.
    assert Scorer(market, xi, hist, current={}).rate(mk("p0")) == full
    assert Scorer(market, xi, hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(mk("p0")) \
        == full

    # THE FIXTURE SPLITS THE TWO DECISIONS. Same player, same inputs; the
    # fielding number moves with the opponent and the buying number does not.
    when = __import__("datetime").datetime.fromisoformat(
        "2026-08-20T19:00:00+00:00")
    easy = Match("Elche", True, when, 1.10, 20, 20)
    sc3 = Scorer(market, xi, hist, board={"Mid": easy})
    s = sc3.score(mk("p0"))
    assert abs(s.flat - full.ppm) < 1e-9              # P(start) is 100%
    assert abs(s.score - full.ppm * 1.10) < 1e-9
    assert s.opp == "Elche" and s.home and s.fix == 1.10
    # No fixture for his team: neutral, and the report can see it is unknown.
    solo = Scorer(market, xi, hist, board={}).score(mk("p0"))
    assert solo.fix == 1.0 and solo.opp == "" and solo.score == solo.flat

    # An absence is an absence in BOTH numbers — a suspended player is not a
    # cheap fielding risk on a kind fixture.
    out = [{"player_name": "p0", "start_pct": "100", "status": "suspended"}]
    zero = Scorer(market, out, hist, board={"Mid": easy}).score(mk("p0"))
    assert zero.score == 0.0 and zero.flat == 0.0
    # A doubt halves both.
    dbt = [{"player_name": "p0", "start_pct": "100", "status": "doubt"}]
    half = Scorer(market, dbt, hist, board={"Mid": easy}).score(mk("p0"))
    assert abs(half.flat - full.ppm * DOUBT_FACTOR) < 1e-9
    assert abs(half.score - full.ppm * 1.10 * DOUBT_FACTOR) < 1e-9

    # pick_xi still ranks on `score`, so the fixture reaches the eleven it is
    # meant to reach, and as_row() carries the new fields to the renderers.
    assert "fix" in s.as_row() and "flat" in s.as_row()

    print("ffcore.score self-test OK (22 cases)")


if __name__ == "__main__":
    _selftest()
