"""Plan 5.7 (1): do rivals buy what the model likes?

    PYTHONPATH=src python tools/analyses/rival_signal.py

Each purchase in the ledger, set at the moment of the nearest earlier replayed
forecast (replay_forecast, live): the share of all players the bought player's
predicted points beat. A random buy would sit at 0.5; 0.80 on average.
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt

import numpy as np

from ffcore.db import connect


def main() -> None:
    c = connect()
    buys = c.execute("""SELECT s.manager, a.player, a.at FROM current_api_activity a
        JOIN (SELECT DISTINCT user_id, manager FROM current_api_standings) s ON s.user_id = a.user_id
        WHERE a.kind = 'buy' AND a.player IS NOT NULL""").fetchall()
    snaps = [r[0] for r in c.execute("SELECT DISTINCT as_of FROM replay_forecast WHERE variant = 'live' ORDER BY 1")]
    when = [dt.datetime.strptime(s, "%Y-%m-%dT%H%MZ").replace(tzinfo=dt.timezone.utc) for s in snaps]
    pred: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for as_of, player, p in c.execute("SELECT as_of, player, predicted FROM replay_forecast WHERE variant = 'live'"):
        pred[as_of][player] = p
    shares: dict[str, list[float]] = collections.defaultdict(list)
    for manager, player, at in buys:
        i = bisect.bisect_right(when, dt.datetime.fromisoformat(at).astimezone(dt.timezone.utc)) - 1
        if i >= 0 and player in pred[snaps[i]]:
            values = np.array(list(pred[snaps[i]].values()))
            shares[manager].append(float((values < pred[snaps[i]][player]).mean()))
    every = [x for v in shares.values() for x in v]
    print("%d purchases: mean %.2f, median %.2f" % (len(every), np.mean(every), np.median(every)))
    for manager, v in sorted(shares.items()):
        print("  %-18s %3d  %.2f" % (manager, len(v), np.mean(v)))


if __name__ == "__main__":
    main()
