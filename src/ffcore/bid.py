"""
ffcore.bid — what a player adds to your XI, and what it takes to win him.

Issue #23: the old reading of the ledger was inverted. rivals.py classified a
price by whether it was a round number and concluded that an exact one was
"the app's own valuation, which means nobody competed — every exact purchase
was a player you could have had for the same money." The repo's own ledger
says otherwise: of the ten priced buys on it when that was written, the five
exact-priced ones went for +1.5%, +2.6%, +2.6%, +9.2% and +12.7%. None was the
app's valuation, so none was available at the floor.

Roundness cannot carry that inference, in either direction:

  * A round price is indeed human-chosen — only 0.7% of the 610 current market
    values are divisible by 10k, so the app almost never hands you one. That
    half of the old heuristic survives, as an observation about how they type.
  * A non-round price is NOT the app's valuation. It is a human who typed a
    non-round number, and the premium column two cells away already says how
    far above the floor they went. The proxy adds nothing the direct
    measurement doesn't say better.
  * Even a purchase at exactly the floor would not prove nobody competed. A
    sealed bid is paid as bid, so matching it wins only if the tie-break
    favours you, and the tie-break rule is not documented anywhere we can
    read. Verify it in-app before treating a floor price as a missed bargain.

So the signal is the premium over the floor, which the ledger measures
directly:

    floor    = today's market value. The minimum legal bid IS the value.
    premium  = price / value_at_the_time - 1

    prem = premiums(deals)                  # what winning has cost so far
    adv  = suggest(value, prem, cash, ceil)  # what to bid for this one
    g    = gain(pool, candidate, xi_total)   # what he adds if you play him
    got, line, spent, forgone = basket(free, cash)   # what cash itself buys
    ratio_of(g, cost_of(value, prem)) > line          # the whole rule

NOTHING HERE ASSERTS HOW OFTEN THE FLOOR WINS. An earlier version of this
module said it never had, on the strength of ten buys that all cleared the
value. Ten rows later three buys had gone at exactly the value, and the
sentence was still in the report, printed as fact. `Premiums.at_floor` now
counts them so the reports state the current number instead. When the ledger
contradicts the prose, the prose is the bug.

THE APP RANDOMISES ITS OWN PRICE (issue #23, second half). Selling to the
market does not pay the value: `premiums(deals, "sell")` over the twelve
priced sells in this ledger spans -9.4% to +9.8%, five below and seven above,
which is the value plus or minus a tenth and not a valuation. Two consequences:

  * A sale is a coin flip worth about a tenth of the player either way, so
    never treat the value as the money a sale will raise.
  * It is the closest thing to a P(win) curve available, and it is not one.
    Whether the same randomiser also bids against you for a free agent is
    INFERRED, NOT MEASURED — every deal in the ledger is a winning bid, so
    nothing here has ever observed a bid that lost.

A HANDFUL OF DEALS IS NOT A DISTRIBUTION. Everything premiums() returns is a
summary of purchases made in the first fortnight of a season, so suggest()
reports the range alongside the median and the reports print both. Treat the
band as "what this league has done so far", never as a probability of winning.

`gain` is the marginal-value primitive from docs/design.md §6.3, at the only
precision the data currently supports: the change in the XI ranking index from
owning him. It is not euros per point, and it is not a forecast.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import NamedTuple

from ffcore.league import MARKET
from ffcore.parse import money, ratio
from ffcore.score import SLOT_MIN, THIN, pick_xi, squad_pool
from ffcore.tidy import ledger_stamp

__all__ = ["MAX_LAG_H", "ROUND_TO", "FLOOR_EPS", "HORIZONS", "Premiums", "Advice",
           "is_round", "deals", "usable", "premiums", "suggest", "gain",
           "rivals_short", "xi_snapshots", "demand_summary",
           "cost_of", "ratio_of", "basket",
           ]

# How long after a deal to look at what the price did. Three, seven and
# fourteen days: long enough to see a market react, short enough to still be
# inside the snapshot history early in a season.
HORIZONS = (3, 7, 14)

# A premium computed against a snapshot further than this from the deal is
# reported but never averaged in. Lived in rivals.py; here so report.py sizes
# bids off the same filtered set rivals.py reports.
MAX_LAG_H = 36.0

# A bid divisible by this was typed by a human rather than computed by the app.
# That is all it tells you — see the note on issue #23 above.
ROUND_TO = 10_000

# A premium inside this band is a bid at the floor. The residue is float noise
# plus the euro-rounding in a scraped value, and a buy cannot legally go below
# the value, so a small negative is snapshot lag rather than a cheaper bid.
FLOOR_EPS = 0.5


class Premiums(NamedTuple):
    """What winning has cost, over the floor, in percent."""
    n: int
    median: float
    lo: float
    hi: float
    at_floor: int = 0

    def label(self) -> str:
        return "median %+.1f%%, %+.1f%% to %+.1f%% (n=%d)" % (
            self.median, self.lo, self.hi, self.n)

    def swing(self) -> float:
        """Widest deviation from the floor, either way, in percent.

        The number that describes a price scattered around the value, where a
        median near zero says nothing about how far a single one can land.
        """
        return max(abs(self.lo), abs(self.hi))


class Advice(NamedTuple):
    """A bid band, or (None, None) when you cannot reach the floor."""
    low: float | None
    high: float | None
    why: str


def is_round(price) -> bool:
    """Was this price typed by a human? Says nothing about who competed."""
    v = money(price)
    return v is not None and v > 0 and v % ROUND_TO == 0


def deals(lg, market) -> list[dict]:
    """Every priced ledger row, valued against the market at the time.

    Lived in rivals.py. Here because report.py needs the same premiums to size
    a bid, and two copies of this join would eventually disagree about what a
    deal cost. Rows the market cannot price are returned with value/premium
    None rather than dropped — usable() decides what averages in.
    """
    rows = []
    for t in lg.txns:
        price = money(t.get("price"))
        when = ledger_stamp(t.get("date", ""))
        if price is None or when is None:
            continue
        v = market.at(t["player"], when)
        src = (t.get("from") or "").strip() or MARKET
        dst = (t.get("to") or "").strip() or MARKET
        rows.append({
            "date": t.get("date", ""), "player": t["player"],
            "actor": dst if dst != MARKET else src,
            "side": "buy" if dst != MARKET else "sell",
            "price": price, "when": when,
            "value": v.value if v else None,
            "lag_h": v.lag_h if v else None,
            "premium": ((price / v.value - 1) * 100.0
                        if v and v.value else None),
            "round": is_round(t.get("price")),
        })
    return rows


def usable(d) -> bool:
    """Is this deal's premium priced closely enough in time to average in?"""
    return (d.get("premium") is not None and d.get("lag_h") is not None
            and abs(d["lag_h"]) <= MAX_LAG_H)


def premiums(deals, side: str = "buy") -> Premiums | None:
    """Premium over the floor across every usable deal, or None if none are.

    Buys by default, because what a rival paid over the floor is what it takes
    to outbid one. Ask for `side="sell"` to measure something different and
    just as useful: what the app itself pays, which is NOT the value — see the
    module note on issue #23.

    `at_floor` counts deals priced at or below the value, which reads as a bid
    at the floor on the buy side and as the app underpaying you on the sell
    side. It exists so the reports state how often the floor has won instead of
    asserting it never has.

    It is a share of PURCHASES, never a probability of winning. Every row in
    the ledger is a bid that won, so nothing here can say how often a floor bid
    loses — that is the sampling error issue #23 is about, and dividing
    at_floor by n would reintroduce it wearing a percent sign.
    """
    vals = sorted(d["premium"] for d in deals
                  if d.get("side") == side and usable(d))
    if not vals:
        return None
    return Premiums(len(vals), statistics.median(vals), vals[0], vals[-1],
                    sum(1 for v in vals if v <= FLOOR_EPS))


def suggest(value, prem: Premiums | None, cash=None,
            rival_max=None) -> Advice:
    """What to bid for a player worth `value`.

    `rival_max` is the largest amount any rival could still spend, and must be
    None unless every rival's cash is known — an unknown balance is not a
    zero, and treating it as one is what would tell you to bid the floor
    against someone who can outspend you.
    """
    if not value or value <= 0:
        return Advice(None, None, "no value on record")
    if cash is not None and cash < value:
        return Advice(None, None, "%.2fM short of the floor"
                      % ((value - cash) / 1e6))
    if rival_max is not None and rival_max < value:
        return Advice(value, value, "floor — no rival can afford it")
    if prem is None:
        return Advice(value, value, "floor — no premium history yet")

    low = value * (1 + prem.median / 100.0)
    high = value * (1 + prem.hi / 100.0)
    why = "floor %+.1f%% median, %+.1f%% worst (n=%d)" % (
        prem.median, prem.hi, prem.n)
    if cash is not None and cash < high:
        high = cash
        low = min(low, cash)
        why += ", capped by your cash"
    return Advice(low, high, why)


# ---------------------------------------------------------------------------
# What owning him actually costs
#
# The price is not the cost. A purchase is closer to a loan than a spend: the
# player carries a market value you get back when you sell, so what the deal
# really costs you is the friction — what you pay over the floor going in, and
# what the value does while you hold him.
#
# The exit is deliberately NOT folded into that number. The app pays value give
# or take a tenth, and on a large player that swing is bigger than every other
# term combined; averaging it to zero would hide the only figure here big
# enough to change a decision. It is reported as a band alongside.
# ---------------------------------------------------------------------------

HOLD_DAYS = 14            # a fortnight — roughly two jornadas at this stage


class Friction(NamedTuple):
    """What a hold is expected to cost, in euros. Positive is a cost."""
    entry: float          # paid over the floor
    carry: float          # expected value lost while held
    swing: float          # ± on exit, NOT included in expected
    days: int
    n_drift: int

    @property
    def expected(self) -> float:
        return self.entry + self.carry

    def per_point(self, gain_pts) -> float | None:
        """Expected friction per marginal point per jornada, or None.

        None when the player does not improve the XI: dividing a cost by a
        gain of zero or less produces a number that looks like value and is
        not one.
        """
        if not gain_pts or gain_pts <= 0:
            return None
        return self.expected / gain_pts


# Cheap players and expensive ones do not drift alike — across the snapshots
# so far the under-2M band loses about half a percent a day while the over-30M
# band loses a fifth of that. Averaging them together and applying the result
# to a 58M player roughly doubles his carry cost, which is the difference
# between a buy and a pass. Banded by value, therefore.
DRIFT_BANDS = ((0, 2e6), (2e6, 10e6), (10e6, 30e6), (30e6, float("inf")))


def band_of(value) -> tuple:
    """The drift band a player's value falls in."""
    v = money(value) or 0.0
    for lo, hi in DRIFT_BANDS:
        if lo <= v < hi:
            return (lo, hi)
    return DRIFT_BANDS[-1]


def drift_daily(rows, band=None) -> tuple[float, int]:
    """(mean daily % value move, n) across the market readings given.

    Deliberately a plain mean within a band, not a per-player fit: with a
    handful of days of readings there is no per-player trend to fit, and
    pretending otherwise would dress noise up as a forecast.
    """
    vals = []
    for r in rows:
        p = ratio(r.get("delta_pct_1d"))
        if p is None:
            continue
        if band is not None and band_of(r.get("value")) != band:
            continue
        vals.append(p)
    if not vals:
        return 0.0, 0
    return statistics.mean(vals), len(vals)


def drift_bands(rows) -> dict:
    """{band: (mean daily %, n)} — computed once, looked up per candidate.

    A band with too few readings to mean anything falls back to the whole
    market rather than reporting a mean of three numbers as a rate.
    """
    overall = drift_daily(rows)
    out = {}
    for band in DRIFT_BANDS:
        d, n = drift_daily(rows, band)
        out[band] = (d, n) if n >= MIN_DRIFT_N else overall
    return out


MIN_DRIFT_N = 50


def friction(value, buy_prem: Premiums | None, sell_prem: Premiums | None,
             daily_pct: float = 0.0, n_drift: int = 0,
             days: int = HOLD_DAYS) -> Friction | None:
    """Expected cost of owning a player worth `value` for `days`."""
    if not value or value <= 0:
        return None
    entry = value * (buy_prem.median / 100.0) if buy_prem else 0.0
    carry = -value * (daily_pct / 100.0) * days
    swing = value * (sell_prem.swing() / 100.0) if sell_prem else 0.0
    return Friction(entry, carry, swing, days, n_drift)
def cost_of(value, prem: Premiums | None = None) -> float | None:
    """What winning him is expected to cost: the floor plus the going premium.

    None when there is no value to bid against — never zero, because a zero
    cost divides into an infinite ratio and would buy anything.
    """
    v = value if isinstance(value, (int, float)) else money(value)
    if not v or v <= 0:
        return None
    return v * (1.0 + prem.median / 100.0) if prem else float(v)


def ratio_of(gain_pts, cost) -> float | None:
    """XI index points per million euros. The one currency.

    None when either side is missing. A NEGATIVE ratio is a real answer: it
    prices a player who makes your eleven worse, which is what a cover buy is.
    """
    if gain_pts is None or not cost or cost <= 0:
        return None
    return gain_pts / (cost / 1e6)
def basket(free: list[dict], cash) -> tuple[list[dict], float | None,
                                            float, float]:
    """What idle cash can buy on the replacement scale, best rate first.

    Returns (bought, hurdle, spent, forgone):

      bought   the rows the walk would take, in the order it takes them
      hurdle   the worst rate it funded — the rate to beat. A player you own
               below it can be sold and the money moved into this basket, which
               is what makes one number serve both sides of the market.
      spent    what the basket costs
      forgone  the points above replacement the cash is NOT earning while it
               sits there. This is the cost of idle cash, and it is a number
               rather than a warning about opportunity.

    Rows need `vor` and `value`. Unlike frontier(), nothing here re-picks an
    eleven between purchases: a fixed baseline means two signings do not
    compete for the same slot, so the walk is a sort and a running total.
    """
    rated = []
    for r in free:
        val = r.get("value")
        if not val or r.get("vor") is None or r["vor"] <= 0:
            continue
        rated.append((r["vor"] / (val / 1e6), r))
    rated.sort(key=lambda t: -t[0])

    left = cash or 0.0
    bought, hurdle, spent, forgone = [], None, 0.0, 0.0
    for rate, r in rated:
        if r["value"] > left:
            continue
        left -= r["value"]
        spent += r["value"]
        forgone += r["vor"]
        hurdle = rate
        bought.append(r)
    return bought, hurdle, spent, forgone


def gain(pool: dict, candidate: dict, base_total: float,
         premium: bool = False):
    """XI index gained by owning `candidate`, or None if he can never start.

    Negative is a real answer: it means the best XI containing him is worse
    than the one you already field, so he is squad depth, not an upgrade.
    """
    slot = candidate.get("slot")
    if not slot:
        return None
    # Copy: the caller's pool feeds the bench table, and a candidate left in
    # it would be reported as a player you own.
    trial = {k: list(v) for k, v in pool.items()}
    trial.setdefault(slot, []).append(candidate)
    trial[slot].sort(key=lambda p: p["score"], reverse=True)
    best = pick_xi(trial, force=candidate, premium=premium)
    return None if best is None else best[0] - base_total


def rivals_short(lg, sc, by_key) -> dict[str, list[str]]:
    """{slot: [rival handles thin there]} — who is likely to bid against you.

    You are excluded: the question this answers is who competes, and your own
    shortage is already the reason you are looking.
    """
    out: dict[str, list[str]] = {}
    for m in lg:
        if m.handle == lg.cfg.me:
            continue
        scored, _ = sc.score_squad(
            [by_key.get(k, {}).get("name", k) for k in m.players])
        counts = Counter(p.slot for p in scored if p.slot)
        for slot, floor in THIN.items():
            if counts.get(slot, 0) < floor:
                out.setdefault(slot, []).append(m.handle)
    return out


def xi_snapshots(lg, sc, by_key) -> dict[str, dict]:
    """{handle: {pool, best, missing, short}} under the one shared scorer.

    The one computation of every manager's best XI, shared by report.py's
    slate and rivals.py's matrix and projections — two copies of this
    arithmetic would drift, and a slate priced against a different XI than
    section 6 prints is exactly the inconsistency this exists to prevent.

    `best` is pick_xi's (total, shape, picked) or None — and None is a
    finding, not an error: that manager cannot field a legal XI today.
    `short` lists slots below the legal minimum, where their next purchase
    is forced to go.
    """
    out = {}
    for m in lg:
        names = [by_key.get(k, {}).get("name", k) for k in m.players]
        scored, missing = sc.score_squad(names)
        pool = squad_pool(scored)
        counts = Counter(p.slot for p in scored if p.slot)
        out[m.handle] = {
            "pool": pool,
            "best": pick_xi(pool) if scored else None,
            "missing": missing,
            "short": [sl for sl, need in SLOT_MIN.items()
                      if counts.get(sl, 0) < need],
        }
    return out


def demand_summary(cand: dict, lg, snaps: dict) -> str:
    """Rival demand for one candidate, in one table cell.

    Demand is XI gain, not roster count: a rival \'wants\' him if owning him
    improves their best eleven. Cash then splits the wanters into threats
    and noise:

        Magic +3.2?      strongest threat first; ? = their cash is unknown
        Burton needs     cannot field a legal XI without buying this slot
        (2 broke)        want him, but estimated cash cannot pay the floor
        none             improves nobody\'s XI but yours

    A threat with unknown cash is still a threat — unknown is not zero.
    """
    threats, broke = [], 0
    for m in lg:
        if m.handle == lg.cfg.me:
            continue
        snap = snaps[m.handle]
        if snap["best"] is None:
            if cand.get("slot") in snap["short"]:
                threats.append((float("inf"), m.handle, "needs"))
            continue
        g = gain(snap["pool"], cand, snap["best"][0])
        if g is None or g <= 0:
            continue
        if m.max_bid is not None and m.max_bid < (cand.get("value") or 0):
            broke += 1
        else:
            threats.append((g, m.handle,
                            "?" if m.max_bid is None else ""))
    threats.sort(key=lambda t: -t[0])

    bits = []
    if threats:
        g, h, mark = threats[0]
        head = h.split()[0][:8]
        bits.append("%s needs" % head if mark == "needs"
                    else "%s %+.1f%s" % (head, g, mark))
        if len(threats) > 1:
            bits.append("+%d more" % (len(threats) - 1))
    if broke:
        bits.append("(%d broke)" % broke)
    return ", ".join(bits) if bits else "none"



# ---------------------------------------------------------------------------

def _selftest() -> None:
    # -- premiums ---------------------------------------------------------
    fixed = [
        {"side": "buy", "premium": 2.0, "lag_h": 0.1},
        {"side": "buy", "premium": 10.0, "lag_h": 8.8},
        {"side": "buy", "premium": 20.0, "lag_h": -2.0},   # backwards, still close
        {"side": "buy", "premium": 99.0, "lag_h": 100.0},  # too far to price
        {"side": "buy", "premium": None, "lag_h": 1.0},    # never priced
        {"side": "sell", "premium": 50.0, "lag_h": 1.0},   # the app's side
    ]
    p = premiums(fixed)
    assert p.n == 3, p
    assert p.median == 10.0, p
    assert (p.lo, p.hi) == (2.0, 20.0), p
    assert "n=3" in p.label(), p.label()
    assert premiums([]) is None
    assert premiums([{"side": "buy", "premium": None, "lag_h": 1.0}]) is None

    # Issue #23, second half. Three of this league's fifteen priced buys went
    # at the value itself, so no prose may assert that the floor never wins —
    # the count has to be computed. A small negative is snapshot lag, not a
    # bid below the legal minimum, so it counts as the floor too.
    floors = premiums([{"side": "buy", "premium": x, "lag_h": 1.0}
                       for x in (0.0, -0.19, 4.18, 21.6)])
    assert floors.at_floor == 2, floors
    assert floors.n == 4, floors
    assert premiums(fixed).at_floor == 0, premiums(fixed)

    # The app's own price swings both ways around the value, so the widest
    # deviation is the number that describes it, not the median.
    app = premiums([{"side": "sell", "premium": x, "lag_h": 1.0}
                    for x in (-9.43, 1.53, 9.75)], "sell")
    assert round(app.swing(), 2) == 9.75, app.swing()
    assert round(premiums(fixed).swing(), 1) == 20.0, premiums(fixed)
    # A lag beyond the cut is excluded, not clamped.
    assert usable({"premium": 1.0, "lag_h": MAX_LAG_H}) is True
    assert usable({"premium": 1.0, "lag_h": MAX_LAG_H + 0.1}) is False

    # -- suggest ----------------------------------------------------------
    prem = Premiums(10, 8.9, 1.45, 21.6)

    # The band is the floor plus what this league has actually paid.
    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=5_000_000)
    assert round(a.low) == 1_089_000, a
    assert round(a.high) == 1_216_000, a
    assert "8.9" in a.why and "n=10" in a.why, a.why

    # Nobody can reach the floor: the floor wins, and says why.
    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=900_000)
    assert (a.low, a.high) == (1_000_000, 1_000_000), a
    assert "no rival" in a.why, a.why

    # One unknown rival balance means rival_max is None, and the band stands.
    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=None)
    assert round(a.high) == 1_216_000, a

    # You cannot afford the floor: no band at all, and the shortfall named.
    a = suggest(1_000_000, prem, cash=800_000)
    assert (a.low, a.high) == (None, None), a
    assert "short" in a.why, a.why

    # No ledger yet -> the floor, labelled as ignorance rather than advice.
    a = suggest(1_000_000, None, cash=10_000_000)
    assert (a.low, a.high) == (1_000_000, 1_000_000), a
    assert "no premium history" in a.why, a.why

    # Cash between the floor and the top of the band clamps the band.
    a = suggest(1_000_000, prem, cash=1_100_000)
    assert a.high == 1_100_000, a
    assert round(a.low) == 1_089_000, a
    assert "capped by your cash" in a.why, a.why

    # Cash below the median premium clamps both ends, not just the top.
    a = suggest(1_000_000, prem, cash=1_050_000)
    assert (a.low, a.high) == (1_050_000, 1_050_000), a

    # A player with no value is not biddable.
    assert suggest(None, prem).low is None
    assert suggest(0, prem).low is None

    # -- gain -------------------------------------------------------------
    def pl(slot, score):
        return {"slot": slot, "score": score, "name": "%s%.1f" % (slot, score)}

    # Eleven is eleven: a pool that cannot fill one legal shape tests nothing.
    pool = {
        "POR": [pl("POR", 5.0)],
        "DEF": [pl("DEF", 4.0), pl("DEF", 3.5), pl("DEF", 3.0),
                pl("DEF", 2.5)],
        "MED": [pl("MED", 4.0), pl("MED", 3.5), pl("MED", 3.0),
                pl("MED", 2.5), pl("MED", 2.0)],
        "DEL": [pl("DEL", 6.0), pl("DEL", 1.5), pl("DEL", 1.0)],
    }
    base = pick_xi(pool)
    assert base is not None
    total = base[0]
    assert base[1] == (4, 5, 1) and total == 39.0, base[:2]

    # Better than the man he replaces: a positive gain.
    up = gain(pool, pl("DEL", 9.0), total)
    assert up is not None and up > 0, up

    # Worse than everyone: forcing him in costs you, and that is the answer.
    down = gain(pool, pl("DEL", 0.1), total)
    assert down is not None and down < 0, down

    # No slot -> he can never start, so there is no XI gain to report.
    assert gain(pool, {"slot": "", "score": 9.0}, total) is None

    # The caller's pool is an input, not scratch: mutating it would corrupt
    # the bench table report.py builds from the same dict.
    assert len(pool["DEL"]) == 3, pool["DEL"]
    assert [p["score"] for p in pool["DEF"]] == [4.0, 3.5, 3.0, 2.5], \
        pool["DEF"]

    # A slot the pool has never seen is still scoreable.
    thin = {"POR": [pl("POR", 5.0)], "DEF": [pl("DEF", 4.0)]}
    assert gain(thin, pl("MED", 9.0), 0.0) is None   # still no legal XI

    # -- is_round ---------------------------------------------------------
    assert is_round("1.000.000 €") is True
    assert is_round("1.234.567") is False
    assert is_round("0") is False           # a free transfer is not a bid
    assert is_round("") is False

    # -- deals ------------------------------------------------------------
    class _Val(NamedTuple):
        value: float
        lag_h: float

    class _Market:
        """Every player worth 1M, priced 2h from the deal."""
        def at(self, name, when):
            return None if name == "Unpriced" else _Val(1_000_000.0, 2.0)

    class _Lg:
        txns = [
            # bought from the market at a 10% premium, round number
            {"date": "2026-08-10 12:00", "player": "Bought",
             "from": "", "to": "me", "price": "1.100.000"},
            # sold to a rival: side is sell, actor is the seller's counterparty
            {"date": "2026-08-11 12:00", "player": "Sold",
             "from": "me", "to": "", "price": "900.000"},
            # rival-to-rival, non-round
            {"date": "2026-08-12 12:00", "player": "Traded",
             "from": "a", "to": "b", "price": "1.234.567"},
            {"date": "2026-08-12 12:00", "player": "Unpriced",
             "from": "", "to": "me", "price": "5.000.000"},
            {"date": "2026-08-12 12:00", "player": "Free",
             "from": "", "to": "me", "price": ""},        # no price: skipped
            {"date": "nonsense", "player": "Undated",
             "from": "", "to": "me", "price": "1.000.000"},   # skipped
        ]

    dl = deals(_Lg(), _Market())
    assert [d["player"] for d in dl] == ["Bought", "Sold", "Traded",
                                         "Unpriced"], dl
    d0 = dl[0]
    assert d0["side"] == "buy" and d0["actor"] == "me", d0
    assert round(d0["premium"], 1) == 10.0, d0
    assert d0["round"] is True and usable(d0)
    assert dl[1]["side"] == "sell" and dl[1]["actor"] == "me", dl[1]
    assert dl[2]["actor"] == "b" and dl[2]["round"] is False, dl[2]
    # No snapshot to price against: reported, but never averaged in.
    assert dl[3]["value"] is None and dl[3]["premium"] is None
    assert not usable(dl[3])
    # And the buy premiums flow straight into the band. Both buys count: a
    # rival-to-rival deal is still someone outbidding you.
    pd = premiums(dl)
    assert pd.n == 2, pd
    assert (round(pd.lo, 1), round(pd.hi, 1)) == (10.0, 23.5), pd
    assert round(premiums(dl, "sell").median, 1) == -10.0, premiums(dl, "sell")

        # --- demand_summary ----------------------------------------------------
    class _M:
        def __init__(self, handle, max_bid):
            self.handle, self.max_bid = handle, max_bid

    class _Lg:
        class cfg:
            me = "me"

        def __init__(self, managers):
            self._m = managers

        def __iter__(self):
            return iter(self._m)

    def snap(pool=None, short=()):
        best = pick_xi(pool) if pool else None
        return {"best": best, "pool": pool or {},
                "short": list(short), "missing": []}

    # One MED slot, easily beaten by a 5.0 candidate.
    weak_pool = {"POR": [{"slot": "POR", "score": 5.0}],
                 "DEF": [{"slot": "DEF", "score": 3.0}] * 5,
                 "MED": [{"slot": "MED", "score": 1.0}] * 5,
                 "DEL": [{"slot": "DEL", "score": 2.0}] * 3}
    strong_pool = {k: [dict(p, score=9.0) for p in v]
                   for k, v in weak_pool.items()}
    cand = {"slot": "MED", "score": 5.0, "value": 10e6, "name": "x"}

    lg = _Lg([_M("me", None), _M("Rich Guy", 50e6), _M("Poor", 1e6),
              _M("Mystery", None), _M("Full", 50e6)])
    snaps = {"me": snap(weak_pool), "Rich Guy": snap(weak_pool),
             "Poor": snap(weak_pool), "Mystery": snap(weak_pool),
             "Full": snap(strong_pool)}
    cell = demand_summary(cand, lg, snaps)
    # Rich and Mystery are threats (Mystery marked ?), Poor is broke,
    # Full gains nothing.
    assert "Rich" in cell and "+1 more" in cell and "(1 broke)" in cell, cell
    assert demand_summary(cand, _Lg([_M("me", None), _M("Full", 50e6)]),
                          {"me": snap(weak_pool), "Full": snap(strong_pool)}
                          ) == "none"
    # A manager with no legal XI and a hole in this slot is a forced buyer.
    got = demand_summary(
        cand, _Lg([_M("me", None), _M("Stuck", 1e6)]),
        {"me": snap(weak_pool), "Stuck": snap(short=["MED"])})
    assert got == "Stuck needs", got

    # --- friction ----------------------------------------------------------
    buy = Premiums(21, 2.6, -0.2, 21.6, 6)
    sell = Premiums(13, 3.3, -9.4, 12.0, 5)
    d, n = drift_daily([{"delta_pct_1d": "-0.2"}, {"delta_pct_1d": "-0.4"},
                        {"delta_pct_1d": "0.0"}, {"delta_pct_1d": None},
                        {"delta_pct_1d": "not a number"}])
    assert n == 3 and abs(d - (-0.2)) < 1e-9, (d, n)
    # A market with no readings at all drifts by nothing, rather than crashing.
    assert drift_daily([]) == (0.0, 0)

    # Bands. A cheap player and an expensive one do not drift alike, and
    # applying the pooled mean to the expensive one doubles his carry cost.
    assert band_of(1e6) == (0, 2e6)
    assert band_of(58e6) == (30e6, float("inf"))
    assert band_of(None) == (0, 2e6)
    mkt = ([{"value": 1e6, "delta_pct_1d": "-1.0"}] * 60
           + [{"value": 58e6, "delta_pct_1d": "-0.1"}] * 60)
    cheap, _ = drift_daily(mkt, band_of(1e6))
    dear, _ = drift_daily(mkt, band_of(58e6))
    assert abs(cheap + 1.0) < 1e-9 and abs(dear + 0.1) < 1e-9, (cheap, dear)
    bands = drift_bands(mkt)
    assert abs(bands[band_of(58e6)][0] + 0.1) < 1e-9, bands
    # A band too thin to mean anything falls back to the whole market rather
    # than reporting the mean of three readings as a rate.
    thin = drift_bands(mkt + [{"value": 20e6, "delta_pct_1d": "-9.0"}])
    assert abs(thin[band_of(20e6)][0] - drift_daily(
        mkt + [{"value": 20e6, "delta_pct_1d": "-9.0"}])[0]) < 1e-9, thin

    f = friction(58e6, buy, sell, daily_pct=-0.19, n_drift=2455, days=14)
    assert abs(f.entry - 1.508e6) < 1e3, f.entry          # 2.6% of 58M
    assert abs(f.carry - 1.5428e6) < 1e3, f.carry         # 0.19% x 14 days
    assert abs(f.expected - 3.05e6) < 5e3, f.expected
    # The exit swing is reported, never averaged into the expected cost: at
    # 12% of 58M it is larger than both other terms together, and hiding it
    # inside a single figure would make a coin flip look like a price.
    assert abs(f.swing - 6.96e6) < 1e3, f.swing
    assert f.swing > f.expected

    # Cost per marginal point, and no answer where there is no gain.
    assert abs(f.per_point(2.7) - 1.13e6) < 1e4, f.per_point(2.7)
    assert f.per_point(0) is None and f.per_point(-1.5) is None
    assert f.per_point(None) is None

    # A rising market is a negative carry — the hold pays you.
    up = friction(10e6, buy, sell, daily_pct=+0.5, n_drift=100, days=14)
    assert up.carry < 0, up.carry
    assert friction(0, buy, sell) is None

    # -- the basket your idle cash can actually buy -------------------------
    # Rows priced on the replacement scale, so a rate is points above the level
    # the market supplies for free, per million. No pick_xi and no base total:
    # that is the point of the fixed baseline — two purchases no longer
    # interact, so the walk is arithmetic rather than a search.
    def cand(name, vor_, val):
        return {"name": name, "vor": vor_, "value": val}

    free = [cand("cheap star", 2.0, 10e6),      # 0.200/M
            cand("solid", 1.5, 15e6),           # 0.100/M
            cand("dear", 3.0, 60e6),            # 0.050/M
            cand("filler", 0.01, 5e6),          # 0.002/M
            cand("waste", -1.0, 5e6)]           # negative: never bought
    got, hurdle, spent, forgone = basket(free, 30e6)
    assert [r["name"] for r in got] == ["cheap star", "solid", "filler"], got
    # THE HURDLE is the worst rate you would actually fund, because a player
    # you own below it can be sold and the money moved into this basket.
    assert hurdle == 0.002, hurdle
    assert spent == 30e6 and abs(forgone - 3.51) < 1e-9, (spent, forgone)
    # "dear" is skipped for being unaffordable, not for being bad, and the
    # walk keeps going: greedy by rate, capped by what is left.
    assert all(r["name"] != "dear" for r in got)
    # Negative rates are never bought, so a pool of them buys nothing and the
    # hurdle is None rather than 0 — there is no rate to beat.
    assert basket([cand("waste", -1.0, 5e6)], 30e6) == ([], None, 0.0, 0.0)
    # No cash, no basket: the money is the constraint, and an empty walk says
    # so instead of pricing a purchase you cannot make.
    assert basket(free, 0) == ([], None, 0.0, 0.0)
    # A row with no value cannot be rated and is skipped, not treated as free.
    assert basket([cand("unpriced", 5.0, None)], 30e6) == ([], None, 0.0, 0.0)

    print("ffcore.bid self-test OK (127 cases)")


if __name__ == "__main__":                      # pragma: no cover
    _selftest()
