# liga_five_guys

Advice for a five-manager LaLiga Fantasy league: who to field, who to buy,
and who to sell to pay for them. Personal use; don't
redistribute the scraped data.

## Where it runs

On the Asus box under systemd. `tools/lfg-run` (linked from
`~/.local/bin/lfg-run`) runs the self-tests and the fetch, then `src/run.py`,
then commits. Every run, whoever asks for it (timer, phone button, line-up
reminder), runs every stage of `src/run.py` in one process; the report is
published to the phone (notes.lemonworlds.com/fantasy) as soon as it is made. The phone's "Run again" button leaves
a request that `lfg-watch.timer` picks up within a minute.

    systemctl --user list-timers | grep lfg     # lfg, lfg-flip, lfg-watch, lfg-gc
    systemctl --user start lfg.service          # run now
    journalctl --user -u lfg.service -n 50      # what happened

`lfg.service` runs with MemoryMax=750M and a 10-minute timeout. The timers
run the working tree, so keep it green: work in a worktree, check
`systemctl --user is-active lfg.service` before merging. Give a worktree its
own `.venv` (`uv sync` in it), never a link to this one: `uv run` installs
the project it runs in into the venv it uses, so a linked venv runs the
timers on the worktree's code.

## What you edit

`inputs/league.ini` (who you are and the budget). Every ~90
days, or when the report says so: `python -m ffcore.auth --login`.
Everything else comes from the app's API and public pages.

## The pipeline (`src/run.py`)

1. **parse** (`sources.py`, `ingest.py`): every fetched page is archived in
   `data/raw/`; parsers turn it into tables in `data/lfg.db`. `sources.py` says
   what to fetch; the parsers live with their provider (`ffcore/futbolfantasy`,
   `ffcore/footballdata`, `ffcore/laliga_api`). Each cached page is keyed by a
   fingerprint of its parser's code, so editing one parser re-parses only its
   pages.
2. **crosswalk** (`crosswalk.py`): the registry, lfg.db's `crosswalk` table,
   one id per player (FF's `ff_id`) with the app's and FF's slugs attached.
3. **sim** (`sim.py`, `decision.py`, `ffcore/decide.sql`): the decision,
   and what the board reads: the run's moves, the facts it saw (`sim_facts`,
   `sim_waiting`), its forecast moment (`sim_runs.moment`).
4. **publish** (`publish.py`): the board (`board_json`) to the phone, and its ping.
5. **grade** (`grading.py`: each past lock forecast and decided again with
   this code, one set kept a code version; the forecasts graded are the
   lock moments, `grade_moment`, read through `grade_forecast`), **health**
   (`health.py`). A failure from here on is noted and the run goes on;
   before it, the run stops.

One copy of each fact: what is fetched is ingest's tables; the forecast is
`forecast_cell`, made once a moment (the forecast's code, its constants and
the cut) and kept while a run or a grade points to it; a run records what
it decided and what it read by reference. Everything else is a view.
A past universe is built once and kept (`ffcore/kept.py`), so the live
grade, the backtests, the replays and golden boards share it.

## The model

Every forecast is the same product, for every player and jornada
(`forecast_cell` keeps each factor):

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
- One table, `forecast_cell` (points if he plays, the chance he plays, a
  moment's every player and jornada left), feeds the board and every
  backtest; `forecast_used` is it as used (no chance for a player who
  arrives at a club after its match).
- **Points history** (`ffcore/points.py`, `scored()`): each change in a player's points
  total, given to the latest match his club had played.
- **Uncertainty** (`ffcore/forecast.py`): match-level points as the pool
  of real single-match scores, and one persistent per-player error
  (`PERSISTENT_SHARE`, measured by the backtest). A move's expected gain
  and its chance of helping, and the standings if nobody moves, are
  worked out from them in SQL (`ffcore/gain.py`, docs/notes/gain.md). A
  jornada under way keeps the elevens that fielded it.
- **Prices** (`ffcore/pricing.py`): values keep their trend; the next h
  updates move by a fitted multiple of the last one, h being the longest
  horizon the data can fit (a rise was still carrying there), not the
  lock: a buy pays its premium once and keeps rising after it. A bid's
  chance of winning is read off the 30 past auctions of the players nearest
  in price trend (schema.sql's `bid_odds`, from `auction_results`: what the
  winner paid, or asking if nobody bought): rising players draw rivals'
  bids, falling ones almost never. A buy bids the level whose chance times
  the points the move adds is greatest, the least of those as likely
  (decide.sql's `dec_options`).
  The game offers for each listed player every night, centred on value
  (median 0.99x, 80% within 0.92-1.07x) and fresh each night. A sale gets
  tonight's offer or what waiting is worth, whichever is more
  (`Market.fetches`; schema.sql's `waiting`: each later night's offer, at
  his value then (`price_carry`), taken when it beats waiting on, the last
  before the lock always); with no offer yet, his value.
- **When a sale is made** (decide.sql `dec_when`): at once, but when the
  plan bids, after the auctions, once you know the bids are won (a sale
  made for a bid then lost is a player gone for nobody; after the auctions
  the game still buys him at his value). The board says it, the backtest
  does it. A player bought is listed and sold like any other when an offer
  beats waiting: there is no separate buy to resell.
- **Assembly** (`assemble.py`): the one place that reads the tables and
  builds the scorer, market, league state and forecast; model modules never
  read a table themselves.
- **Moves** (`ffcore/decide.sql`, its maths at the top of the file): the
  aim is to finish first, so every move is worth its points and nothing
  else; money is a limit, never a goal. A buy is its chance of winning
  times the points it adds to the best eleven of each jornada left; a sale
  the points it costs (never more than nothing, so made only to pay for a
  buy, make room for one, or clear a debt); a swap both, on the squad they
  make together. The plan takes the best move worth more
  than nothing, then again on the squad and cash it leaves, while the squad
  fits, cash at the lock stays at zero or more and bids stay within cash
  and the game's debt; in debt, the least bad sale until clear.
  `decision.py` writes the universe's squad, market and forecast as its
  inputs (`dec_*`) and reads the moves back. A player comes from the
  market, at the bid it takes to win the auction; never from a rival by his
  release clause (you do not shoot first). The `Universe` (`decide.py`)
  joins an `Outlook` (`ffcore/outlook.py`: expected points and the best
  eleven, no money) to a `Market` (`ffcore/market.py`: names, prices,
  values, your cash and bids, no points).

  Each line of the board is one row of `board_rows` (schema.sql), worded
  there once: the move, the step it takes in the app and its amount,
  whether it is done, its points and the numbers that made it, and the
  amounts it is accepted at (`accepts`, which act.py, the server and the
  page read). They come from what the run recorded (`decision_option`,
  `decision_plan`, `sim_facts`, `sim_waiting`). The board also lists your
  players a rival can take by clause now (`board_clauses`): all of them,
  since a sale to the game pays at once and any rival can raise a clause
  that way. The page lays rows out and decides nothing.
- **The ledger** (`ffcore/league.py`, `ledger`): the activity feed read
  once, each event keyed like every other table. Rival cash and the price
  paid (each owned player's last buy or transfer to his owner) are both
  read from it (`price_paid` on the box).
- **What the board says** (`board_rows`), one list: the line-up, the
  plan's moves (a buy: what he is listed at and the bid to make; a sale:
  after the auctions, take the offer, wait, or list him), and your players
  not listed, rising first: the game offers only for listed players,
  listing costs nothing and a listed player still plays. No line shows a
  move the plan does not make. Every ranked buy and offer is on the box
  (`market`, `offers`).
- **Rival cash**: budget plus their sales, bonuses and
  transfer income, less buys and transfer payments, plus the income the feed
  never records (your real balance minus the same sum for you).

## League rules

The game's rules as of 2026-09-30, and whether the model needs them. A rule
is in the code only if the feed does not already say it and it changes
which move wins.

- **Clauses are paid at once, with no approval, and the money goes to the
  owner.** The board never pays one; it lists your players a rival can
  take now (`board_clauses`). The feed records any move between
  two managers alike, clause or accepted offer ("transfer"); every raid
  in it paid exactly the clause the feed showed.
- **A clause defaults to purchase price +50%, market value if higher, 1M
  minimum** (the help pages). So a raid costs only market value once a
  player's value has outgrown 1.5x what his owner paid (Raphinha: bought
  for 80.0M, raided at his 141.4M value). Not needed by the model: the
  feed gives each clause, and a raid pays exactly that.
- **No protection after a transfer in this league.** The feed's
  `buyoutClauseLockedEndTime` is not one: Luismi Cruz was taken 6 days and
  Yamal 13 days before it. Every clause counts as payable.
- **Clause buys close before the matchday** (24h by default; 24–72h per
  league). Not modelled: ours paid one 9h before kickoff, and the app
  refuses a closed one anyway.
- **A negative balance at the matchday's start scores 0 points; debt is
  capped at 20% of team value.** Enforced by the plan (decide.sql's
  `dec_next`: cash at the lock at least zero, bids within the debt) and by
  the app (bids). Not modelled for rivals: the standings ignore every
  manager's cash, so a rival below zero at the lock still scores.
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

    python src/grading.py money                   # the backtest: a phantom deciding daily since the first lock,
                                                  # points and money against standing still (LFG_VARIANT=name)
    python src/grading.py variant NAME K=V ...    # a challenger's constants, for money and replay
    python src/grading.py replay [JOBS]           # the forecast at every past line-up snapshot (view replayed)
    bash tools/selftests.sh                       # every module's self-test
    tools/lfg-dry-run [stage ...]      # the full run against a scratch copy; nothing published
    python tools/golden.py freeze|check           # a refactor must rebuild the same board
    uv run mypy src tools                         # strict: every function annotated, no bare
                                                  # dict/list; gates selftests (pyproject [tool.mypy])
    python tools/q.py --time                      # every saved Datasette query, timed

Run them with `PYTHONPATH=src FF_ROOT=<a copy of data>`; rehearse boards on a
copy of `data/`, never the live one. A change goes in when the backtest
says so, judged by squared error (the simulation needs means, and absolute
error rewards forecasts biased low on skewed points). A change that should
not move the board (a refactor) is frozen first and checked against it.
New code asks the `Universe`, `Outlook` or `Market` for what it needs
rather than reaching through them; ruff flags reads of private members.

## Layout

    src/            run, ingest, sources, crosswalk, assemble, decide (Universe),
                    decision (decide.sql's inputs and moves), sim, publish,
                    grading, act, remind, health, stats
    src/ffcore/     schema.sql (the views), decide.sql (the decision),
                    tables.sql and identity.sql (what parse fills), db, kept,
                    tidy (paths, current/history: rows with their numbers
                    parsed, per Table.numbers), clock (now, stamps),
                    jornadas (locks), points (points history),
                    rules (positions, formations, the eleven, the debt),
                    names (Name, PlayerKey, AppId: who a player is),
                    source, futbolfantasy, footballdata, laliga_api, parse,
                    facts, text, auth, crosswalk, league, score, startprob,
                    fixture, schedule, forecast, season, outlook, gain,
                    market, pricing, action, fixtures (test data)
    inputs/         league.ini
    data/raw/       archived pages, append-only
    data/tidy/      parse caches (what parse fills is rebuilt from raw: the
                    registry each run, last season from
                    raw/season=<season>.tar.xz; runs, their decisions,
                    forecasts and grades are records, kept)
    reports/        decisions.json, what the phone draws
