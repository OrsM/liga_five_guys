"""Plan 5.6: how much each of a player's last value changes says of the next.

    PYTHONPATH=src python tools/analyses/price_rule.py

Every player-day whose six previous days are all there, changes clipped at
CLIP; fit on the first 70% of days, scored on the last 30%. Prints the
next-day error by number of lags (0.53 / 0.42 / 0.40 at 1 / 2 / 3, nothing
after), and the paired gain of three lags over one with its interval.
"""
from __future__ import annotations

import collections
import datetime as dt

import numpy as np

from ffcore.db import connect, rows

LAGS = 6


def main() -> None:
    clip = rows("SELECT clip FROM k")[0][0] if rows("SELECT count(*) FROM model_constants")[0][0] else 20.0
    by_player: dict[str, list[tuple[int, float | None]]] = collections.defaultdict(list)
    for ff_id, day, step in connect().execute(
            "SELECT ff_id, substr(observed_at, 1, 10), step FROM price_obs ORDER BY ff_id, observed_at"):
        by_player[ff_id].append((dt.date.fromisoformat(day).toordinal(), step))
    table = []
    for series in by_player.values():
        for i in range(LAGS, len(series)):
            window = [series[i - j] for j in range(LAGS + 1)]
            steps = [s for _d, s in window if s is not None]
            if len(steps) < len(window) or any(window[j][0] - window[j + 1][0] != 1 for j in range(LAGS)):
                continue
            table.append([max(-clip, min(clip, s)) for s in steps] + [series[i][0]])
    data = np.array(table)
    y, day = data[:, 0], data[:, -1]
    train = day <= np.percentile(day, 70)
    test = ~train
    print("%d player-days, %d scored; no change would err %.3f" % (len(y), test.sum(), abs(y[test]).mean()))
    errors = {}
    for k in range(1, LAGS + 1):
        x = np.column_stack([np.ones(len(y))] + [data[:, j] for j in range(1, k + 1)])
        beta = np.linalg.lstsq(x[train], y[train], rcond=None)[0]
        errors[k] = abs(y - x @ beta)[test]
        print("lags %d: mean abs error %.3f  weights %s" % (k, errors[k].mean(), np.round(beta[1:], 3)))
    gain = errors[1] - errors[3]
    se = gain.std(ddof=1) / np.sqrt(len(gain))
    print("three lags over one: %.3f +- %.3f (95%%), n=%d" % (gain.mean(), 1.96 * se, len(gain)))


if __name__ == "__main__":
    main()
