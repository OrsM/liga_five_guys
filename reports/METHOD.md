# Liga Five Guys — how the numbers are made — 2026-09-07 05:51 UTC




## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 4 is half played — 16 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| Jornada 6 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.001 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 34 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=1.0 (still the unfitted default) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1174 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 648 confirmed starts the fitted version did not beat it out of sample (Brier 0.089 fitted vs 0.089 raw) |


## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 145 | 06 Sep 22:44 | fetched 3 minutes ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 157 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 1,474 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 4,724 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 109 | 05 Sep 20:29 | fetched 33 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 785 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 7,154 | 06 Sep 22:44 | fetched 3 minutes ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 11,091 | 07 Sep 05:48 | fetched 3 minutes ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 2,780 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 2,174 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 127,382 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 126,416 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 60,800 | 07 Sep 05:48 | fetched 3 minutes ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 718 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 1,203 | 07 Sep 05:48 | fetched 3 minutes ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 102,329 | 07 Sep 05:48 | fetched 3 minutes ago |

### The model, as configured right now

| Term | Setting | Fitted? |
|---|---|---|
| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | — |
| Shrinkage | K = 8 matches, applied twice: last season toward the positional prior, then this season toward that | yes |
| Fixture band | ±12% across the opponents by rank, not by ratio | **no, a guess** |
| Home advantage | +4% | **no, a guess** |
| Team strength | **Club Elo rating**, a result-based rating with no transfer fees in it | — |
| P(start) read from | `futbolfantasy` | see the Brier table |
| Fixture applies to | fielding only — never a buy, a sale or the line | — |
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 1174 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±16% of a rate across the squads | derived, not fitted |

### How to read the tables

**The ladder** — one table, read top to bottom: a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. `Start` is the probable-XI read, recalibrated and blended, the same figure the forecast multiplies by. `xPts/j` already has that applied. `€` is what you end up with for doing that row, funding included — a SELL row is what it raises, a BUY row is that money minus what he costs, a SAVE row is how far short you are. `Season` is simulated: extra points over the jornadas left, the same seasons with and without the move — on a KEEP or SELL row, "without" is his best REAL replacement, not nothing, so a negative number there can still mean keep him: his own best alternative costs more than he does, not that he scores less than zero. A `*` on a KEEP/SELL row's "vs X" name means the BUY list's own row for X is funded by somebody ELSE's sale, not his — two different players' money legitimately reaching the same man, not an error to reconcile. `pts/M€` is Season per million actually spent — how CHEAPLY a gain arrived, not how big it is, so read Season first: there are only eleven starting shirts, and a tiny gain at a tiny price can still carry a flattering rate. A dash means the move is net cash-neutral-or-positive (nothing to divide by) or, on a SAVE row, a shortfall nobody can act on yet. `Where` names who holds him, and on a payable clause also the rival closest to affording it — a first name and `today`/`~Nd`, an ESTIMATE off their reconstructed balance, the app's daily allowance, and how fast that manager has actually raised money this season, never a prediction he actually wants the player.

**The league table** — `Pts` is the real league total today. `Season` is the simulated total and its 10-90 band — a mean with no band beside it reads as a prediction it is not. `P(above)` is how often the simulation has you finish above them.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 59 |
| Predicted, total | 197 pts |
| Actual, total | 241 pts |
| **Mean absolute error** | **3.2 pts per player-match** |
| Pairs predating the fixture term | 9 of 59 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

_vs. a trivial guess (everyone scores the sample's own mean, 4.1 pts/match, no player identity at all): ours 3.17 MAE, that guess 3.22 MAE — beats it, adding real information._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| under 2 | 17 | 1.6 | 3.2 |
| 2–3 | 9 | 2.6 | 5.2 |
| 3–4 | 18 | 3.5 | 3.9 |
| 4+ | 15 | 5.6 | 4.6 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 27 | 3.3 | 4.0 | -0.7 |
| neutral | 11 | 4.4 | 4.5 | -0.1 |
| easier | 12 | 3.8 | 4.8 | -0.9 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Iñigo Vicente | 3.0 | 12 | -9.0 |
| Beñat Turrientes | 1.1 | 10 | -8.9 |
| Orri Steinn Óskarsson | 1.7 | 10 | -8.3 |
| Íñigo Ruiz de Galarreta | 3.8 | 12 | -8.2 |
| Dani Martínez | 0.5 | 8 | -7.5 |

### Season-long drift

Still the unfitted default (1.00) — not enough graded pairs yet (h1=37, h3=11, need >=20 each) — keeping 1.00.

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 858 confirmed, 5 locked round(s) | | | | |
| analitica | 597 | 84% | 70% | 0.193 |
| futbolfantasy ←read | 1919 | 37% | 37% | 0.083 |
| our forecast | 43 | 71% | 79% | 0.096 |
| analitica — named, no number | 96 | — | 67% | — |

| **starts, same population as our forecast only** — the fair comparison | | | | |
| analitica | 56 | 93% | 75% | 0.226 |
| futbolfantasy ←read | 93 | 68% | 74% | 0.117 |
| our forecast | 43 | 71% | 79% | 0.096 |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 2445 | 81% | 21% | 0.582 |
| futbolfantasy ←read | 8456 | 37% | 10% | 0.238 |
| our forecast | 324 | 64% | 18% | 0.411 |
| analitica — named, no number | 702 | — | 8% | — |
| futbolfantasy — named, no number | 2 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 374 |
| within 10 points of 50%, on appearances | 1786 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._

_Our forecast vs. trivial baselines, n=44: ours 0.099, a flat 50% guess 0.250, a constant 71% guess 0.180 (Brier, lower is better) — beats both, adding real information._
