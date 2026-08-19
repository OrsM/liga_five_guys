# How the forecast works — and how it's doing

## Where the numbers come from

| Table | What it is used for | Fetched from | Rows | Newest row | State |
|---|---|---|--:|---|---|
| api_activity | every transfer, which is what the ledger replays | LaLiga Fantasy API | 973 | 19 Aug 09:11 | ok |
| api_leagues | your cash and the league's id | LaLiga Fantasy API | 16 | 19 Aug 09:11 | ok |
| api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 643 | 19 Aug 09:11 | ok |
| api_players | names for players nobody owns any more | LaLiga Fantasy API | 800 | 19 Aug 09:11 | ok |
| api_teams | all five squads | LaLiga Fantasy API | 1,216 | 19 Aug 09:11 | ok |
| clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run |
| elo | team strength, which ranks the fixture term | api.clubelo.com | 80 | 17 Aug 23:45 | **33 hours stale** — failed the last sweep, 8.1s |
| fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 367 | 19 Aug 09:11 | ok |
| lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 32,322 | 19 Aug 09:11 | ok |
| market | price, value, position, fitness — every player in the game | futbolfantasy.com | 33,167 | 19 Aug 09:11 | ok |
| matches | fixtures, kickoffs, results | futbolfantasy.com | 7,220 | 19 Aug 09:11 | ok |
| players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 648 | — | rebuilt every run |
| points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 100 | 18 Aug 09:41 | ok |
| starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 3,734 | 19 Aug 09:11 | ok |

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

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 2 |
| Predicted, total | 5 pts |
| Actual, total | 14 pts |
| **Mean absolute error** | **4.3 pts per player-match** |
| Pairs predating the fixture term | 0 of 2 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| 2–3 | 2 | 2.7 | 7.0 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 1 | 2.6 | 6.0 | -3.4 |
| easier | 1 | 2.8 | 8.0 | -5.2 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Omar El Hilali | 2.8 | 8 | -5.2 |
| Iñigo Vicente | 2.6 | 6 | -3.4 |

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 110 confirmed, 1 locked round(s) | | | | |
| analitica | 43 | 87% | 79% | 0.134 |
| futbolfantasy ←read | 224 | 41% | 41% | 0.045 |
| analitica — named, no number | 31 | — | 100% | — |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 239 | 86% | 17% | 0.665 |
| futbolfantasy ←read | 856 | 40% | 7% | 0.261 |
| analitica — named, no number | 39 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 49 |
| within 10 points of 50%, on appearances | 220 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._
