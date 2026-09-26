import random
import statistics as st
import sys

sys.path.insert(0, "src")
from ffcore.lineupweight import (GRID, LEAD_H, fit_lineup_weight, mse, pairs,  # noqa: E402
                                 rows)


def main():
    p = pairs(rows())
    cut = int(0.6 * len(p))
    train, test = p[:cut], p[cut:]
    print("%d judged player-matches (%d train, %d test), lineup >= %gh before "
          "the scrape\n" % (len(p), len(train), len(test), LEAD_H))
    print("%-6s %10s %10s" % ("k", "train MSE", "test MSE"))
    for k in GRID:
        print("%-6g %10.3f %10.3f" % (k, mse(train, k), mse(test, k)))
    best, why = fit_lineup_weight()
    print("\n" + why)
    by = {}
    for i, (r, *_x) in enumerate(p):
        by.setdefault(r["key"], []).append(i)
    rng, ids, gains = random.Random(1), list(by), []
    for _ in range(500):
        s = [p[i] for who in rng.choices(ids, k=len(ids)) for i in by[who]]
        gains.append(mse(s, best) - mse(s, 8.0))
    gains.sort()
    print("all rows, bootstrap over players: MSE change at k=%g vs 8: %+.3f "
          "(95%% %+.3f .. %+.3f)" % (best, st.mean(gains), gains[12], gains[487]))


if __name__ == "__main__":
    main()
