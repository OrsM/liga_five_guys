# liga_five_guys

Advice for a five-manager LaLiga Fantasy league: who to field, who to buy
for points, for cash or both, and when to sell. Personal use; don't
redistribute the scraped data.

## Where it runs

On the Asus box under systemd. `~/.local/bin/lfg-run` runs the self-tests,
fetches, runs `src/run.py` and publishes `reports/decisions.json` to the
phone (notes.lemonworlds.com/fantasy). The phone's "Run again" button leaves
a request that `lfg-watch.timer` picks up within a minute.

    systemctl --user list-timers | grep lfg     # lfg, lfg-flip, lfg-watch, lfg-gc
    systemctl --user start lfg.service          # run now
    journalctl --user -u lfg.service -n 50      # what happened

`lfg.service` runs with MemoryMax=750M and a 10-minute timeout. The timers
run the working tree, so keep it green: work in a worktree, check
`systemctl --user is-active lfg.service` before merging.

## What you edit

`inputs/league.ini` (who you are and the budget). Every ~90
days, or when the report says so: `python -m ffcore.auth --login`.
Everything else comes from the app's API and public pages.

## The pipeline (`src/run.py`)

1. **parse** (`sources.py`, `ingest.py`): every fetched page is archived in
   `data/raw/`; parsers turn it into `data/tidy/*.csv`. `sources.py` says
   what to fetch; the parsers live with their provider (`ffcore/futbolfantasy`,
   `ffcore/footballdata`, `ffcore/laliga_api`). Each cached page is keyed by a
   fingerprint of its parser's code, so editing one parser re-parses only its
   pages.
2. **crosswalk** (`crosswalk.py`): `data/tidy/players.csv`, one id per
   player (FF's `ff_id`) with the app's and FF's slugs attached.
3. **sim** (`sim.py`, `decide.py`): builds the board the phone draws.

## The model

- **Points** (`ffcore/score.py`, `ffcore/schedule.py`): points per
  appearance, shrunk toward last season and position priors, times the
  chance of appearing (FF's line-up percentages, calibrated against who
  actually played), times the opponent's attack/defence factor. One table,
  `per_j[jornada][player] = (points, p)`, feeds the board and the backtest.
- **Points history** (`ffcore/points.py`, `scored()`): each change in a player's points
  total, given to the latest match his club had played.
- **Uncertainty** (`ffcore/forecast.py`, `ffcore/season.py`): match-level
  points drawn from the pool of real single-match scores, and one
  persistent per-player error (`PERSISTENT_SHARE`, measured by the
  backtest), drawn once per simulated season.
- **Prices** (`ffcore/pricing.py`): values keep their trend; the next h
  updates move by a fitted multiple of the last one. The auction premium
  is the median winning bid over asking in the last 50 auctions.
- **Assembly** (`assemble.py`): the one place that reads the tables and
  builds the scorer, market, league state and forecast; model modules never
  read a table themselves.
- **Moves** (`decide.py`): the `Universe` joins an `Outlook`
  (`ffcore/outlook.py`: expected points and the best eleven, no money) to
  a `Market` (`ffcore/market.py`: names, prices, values, your cash and
  bids, no points). Every affordable buy, sale and swap is simulated
  and scored in season points: the points it adds plus, at the measured
  points-per-million, the value its players are expected to gain by the
  lock less the premium paid. Rows say whether they are for points, cash
  or both. A bench player is sold when his cash beats what he still adds.
- **Rival cash** (`ffcore/league.py`): budget plus their sales, bonuses and
  clause income, less buys and clause payments, plus the income the feed
  never records (your real balance minus the same sum for you).

## Checking it

    python src/grading.py --backtest [out.json]   # rebuild each past lock's forecast; rmse, bias, top-50, persistence
    python src/grading.py --compare a.json b.json # two backtests, jornada by jornada
    python src/grading.py --prices                # price model walk-forward vs "no change"
    bash tools/selftests.sh                       # every module's self-test
    python tools/golden.py freeze|check           # a refactor must rebuild the same board

Run them with `PYTHONPATH=src FF_ROOT=<a copy of data>`; rehearse boards on a
copy of `data/`, never the live one. A change goes in when the backtest
says so, judged by squared error (the simulation needs means, and absolute
error rewards forecasts biased low on skewed points). A change that should
not move the board (a refactor) is frozen first and checked against it.
New code asks the `Universe`, `Outlook` or `Market` for what it needs
rather than reaching through them; ruff flags reads of private members.

## Layout

    src/            run, ingest, sources, crosswalk, assemble, decide, sim,
                    grading, stats
    src/ffcore/     tidy (paths, CSVs, current/history), clock (now, stamps),
                    jornadas (locks), points (points history), players,
                    rules (slots, formations, minutes),
                    source, futbolfantasy, footballdata, laliga_api, parse,
                    text, auth, crosswalk, league, score, startprob, fixture,
                    schedule, forecast, season, outlook, market, pricing,
                    action, render, fixtures (test data)
    inputs/         league.ini
    data/raw/       archived pages, append-only
    data/tidy/      tables rebuilt from raw; players.csv is tracked
    data/decisions/ cash_price_log.csv (points per million, read back)
    reports/        decisions.json, what the phone draws
