"""Would the market advice in src/flip.py have made money, judged only on what was known each day?

For every past market close it rebuilds the model from rows dated BEFORE that
day, takes the pick flip.picks() logic would have made, and measures what really
happened over the hold it chose -- at the price the winner actually paid where
the feed shows one, else the ask, plus 1% to beat them. Run it as history grows:

    PYTHONPATH=src FF_ROOT=./data uv run python tools/flip_walkforward.py

WHY IT EXISTS. The first version of the strategy was tuned and checked on the
same 44 days it learned from (+20% per trade). Walked forward it is about +6-7%
per trade over ~6 updates on 9-11 trades (2026-09-24): the honest figure, and
what the risk setting in inputs/league.ini should be chosen against as the sample
grows. "predicted" is the model's confident lower bound; "realised" is the raw
drift that followed -- if realised is far BELOW predicted, the model is
overconfident.

Friction (offer discount, auction premium) is measured on the FULL history: a
small leak, because the early days have too few samples to measure it at all.
"""
from __future__ import annotations

import collections
import statistics as st
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
import flip                                                   # noqa: E402
from ffcore.tidy import TIDY, read_csv                        # noqa: E402

WARMUP = 20            # days of history before the first decision is judged


def main() -> None:
    rows, listings, players = (read_csv(TIDY / f) for f in
                               ("market.csv", "api_market.csv", "players.csv"))
    feed = [a for a in read_csv(TIDY / "api_activity.csv") if a["kind"] == "buy"]
    ff_of = {r["app_id"]: r["player_id"] for r in players if r["app_id"]}
    paid = {}
    for a in feed:
        at = datetime.fromisoformat(a["at"]).astimezone(timezone.utc)
        paid[(a["player_id"], at.strftime("%Y-%m-%d"))] = float(a["amount"])
    full = flip.steps(rows)
    days = sorted({d for s in full.values() for d, _ in s})

    close = {}
    for r in listings:
        if r["seller"] != "marketPlayerLeague" or not r["expires_at"]:
            continue
        end = datetime.fromisoformat(r["expires_at"]).astimezone(timezone.utc)
        day = end.strftime("%Y-%m-%d")
        if r["observed_at"] <= end.strftime("%Y-%m-%dT%H%MZ"):
            k = (r["player_id"], day)
            if k not in close or r["observed_at"] > close[k]["observed_at"]:
                close[k] = r

    from ffcore.tidy import Market, load_market_frozen
    mk = Market(load_market_frozen())
    value_at = lambda n, w: (lambda v: v.value if v else None)(
        mk.at(n, datetime.fromisoformat(w).astimezone(timezone.utc)))
    offer = st.mean(flip.offer_ratios(read_csv(TIDY / "api_offers.csv"),
                                      read_csv(TIDY / "api_teams.csv"), value_at))
    premium = st.mean(flip.auction_ratios(listings, feed))

    def realised(ff, day, h):
        s = [c for d, c in full.get(ff, []) if d >= day][:h]
        if len(s) < h:
            return None
        g = 1.0
        for c in s:
            g *= 1 + c / 100
        return g - 1

    out = collections.defaultdict(list)
    judged = [d for d in sorted({d for _, d in close}) if d >= days[WARMUP - 1]]
    for day in judged:
        known = {k: [(d, c) for d, c in s if d < day] for k, s in full.items()}
        outlook = flip.Outlook({k: v for k, v in known.items() if v})
        cands = [(ff_of.get(pid), pid, float(r["sale_price"]),
                  float(r["market_value"])) for (pid, d), r in close.items()
                 if d == day]
        for z in (0.0, 0.5, 1.0, 2.0):
            best = None
            for ff, pid, ask, val in cands:
                s = known.get(ff)
                b = outlook.best(s[-1][1], offer, premium, z) if s else None
                if b is None or b["net"] <= 0:
                    continue
                gain = val * (1 + b["lo"] / 100) * offer - ask * premium
                if best is None or gain > best[0]:
                    best = (gain, ff, pid, ask, val, b)
            if best:
                _, ff, pid, ask, val, b = best
                r = realised(ff, day, b["h"])
                if r is not None:
                    cost = paid.get((pid, day), ask * premium) * 1.01
                    out[z].append((val * (1 + r) * offer / cost - 1, b["h"],
                                   b["lo"], 100 * r))
    print("walk-forward: %d closing days judged (%s .. %s); offers pay %.3fx value, "
          "auctions cost %.3fx ask\n" % (len(judged), judged[0], judged[-1],
                                         offer, premium))
    print("%-5s %6s %9s %8s %8s %6s   predicted (confident low) -> realised drift"
          % ("risk", "trades", "mean net", "median", "%profit", "hold"))
    for z, r in sorted(out.items()):
        print("%-5.1f %6d %8.1f%% %7.1f%% %7.0f%% %6.1f   %+.1f%% -> %+.1f%%"
              % (z, len(r), 100 * st.mean(x[0] for x in r),
                 100 * st.median(x[0] for x in r),
                 100 * sum(1 for x in r if x[0] > 0) / len(r),
                 st.mean(x[1] for x in r), st.mean(x[2] for x in r),
                 st.mean(x[3] for x in r)))


if __name__ == "__main__":
    main()
