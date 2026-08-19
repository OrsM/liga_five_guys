# How the forecast works — and how it's doing

## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 71 | 19 Aug 23:07 | fetched 11 minutes ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 28 | 19 Aug 23:16 | fetched 11 minutes ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 55 | 19 Aug 23:16 | fetched 11 minutes ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 1,074 | 19 Aug 23:16 | fetched 11 minutes ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 58 | 19 Aug 22:20 | fetched 1 hour ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 140 | 19 Aug 23:16 | fetched 11 minutes ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 882 | 19 Aug 22:20 | fetched 11 minutes ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 2,101 | 19 Aug 23:16 | fetched 11 minutes ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 200 | 19 Aug 23:16 | fetched 6 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 547 | 19 Aug 23:16 | fetched 6 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 41,197 | 19 Aug 23:16 | fetched 6 hours ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 40,988 | 19 Aug 23:16 | fetched 11 minutes ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 11,780 | 19 Aug 23:16 | fetched 6 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 657 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 132 | 19 Aug 22:20 | fetched 11 minutes ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 6,470 | 19 Aug 23:16 | fetched 23 hours ago |

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
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 128 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±19% of a rate across the squads | derived, not fitted |

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 5 |
| Predicted, total | 11 pts |
| Actual, total | 36 pts |
| **Mean absolute error** | **5.0 pts per player-match** |
| Pairs predating the fixture term | 1 of 5 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| under 2 | 1 | 0.5 | 8.0 |
| 2–3 | 3 | 2.5 | 6.3 |
| 3–4 | 1 | 3.0 | 9.0 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 2 | 2.4 | 5.5 | -3.1 |
| easier | 2 | 2.9 | 8.5 | -5.6 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Dani Martínez | 0.5 | 8 | -7.5 |
| Robin Le Normand | 3.0 | 9 | -6.0 |
| Omar El Hilali | 2.8 | 8 | -5.2 |
| Iñigo Vicente | 2.6 | 6 | -3.4 |
| Dani Lorenzo | 2.2 | 5 | -2.8 |

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 110 confirmed, 1 locked round(s) | | | | |
| analitica | 43 | 87% | 79% | 0.134 |
| futbolfantasy ←read | 225 | 41% | 40% | 0.045 |
| analitica — named, no number | 31 | — | 100% | — |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 374 | 86% | 13% | 0.689 |
| futbolfantasy ←read | 1297 | 40% | 6% | 0.265 |
| analitica — named, no number | 47 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 49 |
| within 10 points of 50%, on appearances | 337 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._
