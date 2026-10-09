# liga_five_guys

Advice for a five-manager LaLiga Fantasy league: who to field, who to buy,
and who to sell to pay for them. Personal use; don't
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

Every forecast is the same product, for every player and jornada, and
`python tools/ask.py forecast <player>` prints it factor by factor:

    expected points = points per match x fixture x chance he is picked if fit x chance he is fit

- **Points per match** (`ffcore/score.py`, `Scorer.rate`): last season
  pulled toward his position's typical rate (for a promoted club, that rate
  discounted), then this season pulled toward that. Every "pulled toward"
  in the model is `stats.shrink`.
- **Fixture** (`ffcore/fixture.py`): the opponent's attack or defence factor.
- **Picked if fit** (`ffcore/startprob.py`, `StartOdds.picked`): for his next
  match, futbolfantasy's listing (calibrated on fit players) pulled toward
  his record of appearances in matchday squads; later, 60% pulled toward
  that record; with no record, his listing while fit. A flagged player's
  listing is about the injury, so it never counts as selection.
- **Fit** (`StartOdds.fit`): 1 unless flagged. Flagged by futbolfantasy, his
  prognosis ("Duda para la jornada 8", "Baja hasta mediados de octubre";
  a bare status means his next match) places each jornada in a row of the
  availability table, fitted from past prognoses: appearances under that
  prognosis over appearances when listed fit. The LaLiga app adds only who
  has left the league (out for good); its injury flags lag futbolfantasy's.
- One table, `per_j[jornada][player] = (points, p)`, feeds the board and
  the backtest.
- **Points history** (`ffcore/points.py`, `scored()`): each change in a player's points
  total, given to the latest match his club had played.
- **Uncertainty** (`ffcore/forecast.py`, `ffcore/season.py`): match-level
  points drawn from the pool of real single-match scores, and one
  persistent per-player error (`PERSISTENT_SHARE`, measured by the
  backtest), drawn once per simulated season. A jornada under way keeps
  the elevens that fielded it.
- **Prices** (`ffcore/pricing.py`): values keep their trend; the next h
  updates move by a fitted multiple of the last one, h being the longest
  horizon the data can fit (a rise was still carrying there), not the
  lock: a buy pays its premium once and keeps rising after it. A bid is the
  premium over asking at or above 80% of your last 50 winning bids (yours,
  not the league's: the league's are mostly its boldest bidder's).
  The game offers for each listed player every night, centred on value
  (median 0.99x, 80% within 0.92-1.07x) and fresh each night. A sale gets
  tonight's offer or what waiting is worth, whichever is more
  (`Market.fetches`; `Market.waiting`: each later night's offer, at his
  value then (`Momentum.carry`), taken when it beats waiting on, the last
  before the lock always); with no offer yet, his value.
- **Assembly** (`assemble.py`): the one place that reads the tables and
  builds the scorer, market, league state and forecast; model modules never
  read a table themselves.
- **Moves** (`decide.py`): the `Universe` joins an `Outlook`
  (`ffcore/outlook.py`: expected points and the best eleven, no money) to
  a `Market` (`ffcore/market.py`: names, prices, values, your cash, bids
  and debt, no points). A player comes from the market, at the bid it takes
  to win the auction; never from a rival by his release clause (you do
  not shoot first). Every
  recommendation passes the same four steps, `decide.FUNNEL`, drawn in
  `docs/funnel.mmd`:
  1. **candidates**: every sale of a spare, and every player on the market
     you could get. No quicker measure filters them first: the simulated
     rank is the one judgement (a filter on expected points kept 4 of 11
     buys on 2026-10-08, saved 0.1s, and dropped none that passed).
  2. **rank**: each move against doing nothing, in simulated seasons:
     the points it adds, and per million it spends or raises
     (`Move.per_million`).
  3. **verdict**: a gain in the median, and better off in at least 70% of
     seasons (`CONFIDENCE`); below that its gain is noise.
  4. **plan**: the best set that shares no player and, paid for, clears
     the same bar as one move (`verdict` on the whole set) while adding to
     its gain, built best first and best per million first (one dear move can
     crowd out two cheaper ones that gain more), keeping the set that gains
     more. Money has no price of its own: it only bounds the set, by the
     game's one money rule, a balance of at least zero at the lock (below
     it you score nothing). What a debt or a buy leaves below zero is
     raised by the set of ranked sales that covers it at fewest points
     (`decide.raised`).

  `python tools/ask.py why <player>` names the step a player stopped at,
  `ask.py whatif buy X sell Y` scores a move of your own the same way, and
  `ask.py forecast <player>` prints his forecast factor by factor. The
  board also lists your players a rival can take by clause now (`decide.at_risk`):
  all of them, since a sale to the game pays at once and any rival can raise
  a clause that way.
  Each line of the board is one `sim.row`: the move (`Action.label`), the
  step it takes in the app, whether it is done, its points and the numbers
  that made it. The page lays rows out and decides nothing.
- **The ledger** (`ffcore/league.py`, `ledger`): the activity feed read
  once, each event keyed like every other table. Rival cash and the price
  paid (each owned player's last buy or transfer to his owner) are both
  read from it. The
  board's squad shows it beside his value and the points his sale costs
  per million it raises.
- **Rival cash**: budget plus their sales, bonuses and
  transfer income, less buys and transfer payments, plus the income the feed
  never records (your real balance minus the same sum for you).

## League rules

The game's rules as of 2026-09-30, and whether the model needs them. A rule
is in the code only if the feed does not already say it and it changes
which move wins.

- **Clauses are paid at once, with no approval, and the money goes to the
  owner.** The board never pays one; it lists your players a rival can
  take now (`decide.at_risk`). The feed records any move between
  two managers alike, clause or accepted offer ("transfer"); every raid
  in it paid exactly the clause the feed showed.
- **A clause defaults to purchase price +50%, market value if higher, 1M
  minimum** (the help pages). So a raid costs only market value once a
  player's value has outgrown 1.5x what his owner paid (Raphinha: bought
  for 80.0M, raided at his 141.4M value). Not needed by the model: the
  feed gives each clause, and a raid pays exactly that.
- **No protection after a transfer in this league.** The feed's
  `buyoutClauseLockedEndTime` is not one: Luismi Cruz was taken 6 days and
  Yamal 13 days before it. Every clause counts as payable (`open_clauses`).
- **Clause buys close before the matchday** (24h by default; 24–72h per
  league). Not modelled: ours paid one 9h before kickoff, and the app
  refuses a closed one anyway.
- **A negative balance at the matchday's start scores 0 points; debt is
  capped at 20% of team value.** Enforced by the plan (`decide.raised`:
  the same rule that keeps a buy within your cash) and by the app (bids).
  Not modelled for rivals: the simulated seasons ignore every manager's
  cash, so a rival below zero at the lock still scores.
- **Raising a clause costs half the raise** (1M pays for +2M). Not modelled:
  the board lists who is at risk, not what protecting them would cost.
- **Blindaje:** one player per matchday is shielded from clauses for 24h
  (48h in premium leagues). Turned on between matchdays, free. Not modelled,
  and the feed does not show shields: a player listed at risk may be
  shielded.

Sources: LaLiga Fantasy help ([clauses](https://laligafantasy.zendesk.com/hc/en-us/articles/360025683593-How-are-release-clauses-set),
[rulebook](https://laligafantasy.zendesk.com/hc/en-us/articles/360025679853-Rulebook),
[negative balance](https://laligafantasy.zendesk.com/hc/en-us/articles/360007533594-Can-I-keep-a-negative-balance)),
[blindaje](https://www.guiafantasy.com/noticias/1686/como-funciona-el-blindaje-la-nueva-regla-que-revoluciona-laliga-fantasy).

## Checking it

    python src/grading.py --backtest [--ahead H] [out.json]  # rebuild each past lock's forecast: rmse, bias, top-50,
                                                  # persistence; with --ahead, also H jornadas on, flagged players apart
    python src/grading.py --compare a.json b.json # two backtests, jornada by jornada
    python src/grading.py --decisions             # each past lock's board, rebuilt: its moves' forecast vs what happened
    python src/grading.py --prices                # price model walk-forward vs "no change"
    bash tools/selftests.sh                       # every module's self-test
    tools/lfg-dry-run [stage ...|--selftest]      # the full run against a scratch copy; the repo sees no diff
    python tools/golden.py freeze|check           # a refactor must rebuild the same board
    python tools/structure.py [--check]           # coupling numbers; --check gates selftests
    uv run mypy src tools                         # strict: every function annotated, no bare
                                                  # dict/list; gates selftests (pyproject [tool.mypy])
    python tools/uml.py docs                      # regenerate docs/*.mmd from the code
    python tools/ask.py why|whatif|forecast ...   # questions, answered by the code that makes the board

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
    src/ffcore/     tidy (paths, CSVs, current/history: rows with their numbers
                    parsed, per Table.numbers), clock (now, stamps),
                    jornadas (locks), points (points history), players,
                    rules (positions, formations, shortfall, minutes),
                    names (Name, PlayerKey, AppId: who a player is),
                    source, futbolfantasy, footballdata, laliga_api, parse,
                    text, auth, crosswalk, league, score, startprob, fixture,
                    schedule, forecast, season, outlook, market, pricing,
                    action, fixtures (test data)
    inputs/         league.ini
    data/raw/       archived pages, append-only
    data/tidy/      tables rebuilt from raw; players.csv is tracked
    reports/        decisions.json, what the phone draws
