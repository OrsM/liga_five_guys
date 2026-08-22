# Liga Five Guys — how the numbers are made — 2026-08-22 16:16 UTC




## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 26 players you can buy now | +118 | — |
| Wait for the market | a week of new offers | +151 | 62% |
| Wait for the clauses | 57 players on 24 Aug | +216 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 195 of 589 |
| Tenth percentile of a week's waiting | +1.16 |
| Market model | the market is modelled from 130 offers over 8 cycles, weighted by value^0.15, and only just — value^0.40 fits within 11.7% of it, so read the exponent as roughly this, not exactly this |
| Locked players who would improve your eleven | 43 |
| Their clauses open | 24 Aug, in about 2 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Lamine Yamal | +7.37 | 14 days |
| Joan Garcia | +6.61 | 15 days |
| Kylian Mbappe | +5.98 | 13 days |
| Zaid Romero | +5.84 | 17 days |


## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 1 is half played — 12 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| Jornada 2 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| P(start) is today's, held flat over every remaining jornada | nothing here knows who will be injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread rests on one hand-picked constant (DRIFT_FRAC=2.0), not a fit | two real anchors on this repo's own data disagree on the exact magnitude (weak jornada-1-vs-final correlation argues wider, strong season-to-season correlation argues narrower), but every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless — that floor doesn't need the two anchors resolved. Widened from a prior setting that read 72% to land near a coin flip instead. Tighten only once the Forecast vs actual table above (the real, realised-error check) has enough rows (n=15-20+) to say the model is already well-calibrated |
| Shape prior | shape from the seed prior (189 observed, 200 needed) |
| P(start) fit | P(start) fitted on 362 confirmed starts across 12 team sheets: futbolfantasy recalibrated (logit -0.5 +1.4x), blended 40% with analiticafantasy where it has an opinion (a named starter counts 82%). Brier improves 0.004 on line-ups the fit had not seen |


## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 82 | 21 Aug 21:27 | fetched 1 minute ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 80 | 22 Aug 16:15 | fetched 1 minute ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 627 | 22 Aug 16:15 | fetched 1 minute ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 2,554 | 22 Aug 16:15 | fetched 1 minute ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 68 | 21 Aug 21:27 | fetched 19 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 400 | 22 Aug 16:15 | fetched 1 minute ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 1,176 | 21 Aug 21:27 | fetched 1 minute ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 5,731 | 22 Aug 16:15 | fetched 1 minute ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 1,240 | 22 Aug 16:15 | fetched 9 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 1,268 | 22 Aug 16:15 | fetched 9 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 79,260 | 22 Aug 16:15 | fetched 1 hour ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 75,074 | 22 Aug 16:15 | fetched 1 minute ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 31,540 | 22 Aug 16:15 | fetched 9 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 663 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 193 | 22 Aug 07:13 | fetched 1 minute ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 20,994 | 22 Aug 16:15 | fetched 9 hours ago |

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
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 189 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±19% of a rate across the squads | derived, not fitted |

### How to read the tables

**Field these eleven** — `pts/m` is points per match, last season shrunk toward the average and blended with this season as it accrues (`~` = no record at all, the baseline is assumed). `Fix` is how much the next opponent moves it (`=` a median team, `—` no fixture known). `FF`/`AF` are separate, never blended, because neither has been checked against a played jornada yet and a disagreement is worth more than an average. `xPts/j` = pts/m × Fix × FF, and uses FF only. `⚠` on a name means the Fitness or Starting section has something on him. On a `+SLOT` row, xPts/j is the change to the WHOLE eleven if you sign him and re-pick the shape — his own score, fixture excluded, since you'd own him for months, not one round.

**What to bid** — `Bid` is what it costs to win him, never whether he is worth winning (that is the eleven table). A purchase is closer to a loan than a spend: the value comes back on sale, give or take a tenth, so a bid within a few percent is not a decision. It is priced as the floor plus what this league has actually paid over it — the range is what has happened, not a chance of winning, and every one of them is a bid that won. `XI` is two readings, printed side by side. **FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both. `Competition` is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` means their cash is unknown (treat as live), `(n broke)` means they want him but cannot pay the floor.

**Fitness** — FF's read is from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page; `Tocado` — a knock the site still lists as available — is folded into doubt. No entry is an absence of evidence, not evidence of fitness. 'App' is the game's own operator-stated availability, shown only when it differs from FF's read.

**Starting** — both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` means not on the page at all (assumed 15%). The under-threshold cutoff is `min_start` in `inputs/league.ini`.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 9 |
| Predicted, total | 22 pts |
| Actual, total | 50 pts |
| **Mean absolute error** | **3.5 pts per player-match** |
| Pairs predating the fixture term | 2 of 9 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| under 2 | 4 | 1.3 | 3.0 |
| 2–3 | 3 | 2.5 | 6.3 |
| 3–4 | 1 | 3.0 | 9.0 |
| 4+ | 1 | 6.4 | 10.0 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 4 | 2.0 | 3.5 | -1.5 |
| neutral | 1 | 6.4 | 10.0 | -3.6 |
| easier | 2 | 2.9 | 8.5 | -5.6 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Dani Martínez | 0.5 | 8 | -7.5 |
| Robin Le Normand | 3.0 | 9 | -6.0 |
| Omar El Hilali | 2.8 | 8 | -5.2 |
| Pablo Fornals | 6.4 | 10 | -3.6 |
| Iñigo Vicente | 2.6 | 6 | -3.4 |

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 154 confirmed, 2 locked round(s) | | | | |
| analitica | 71 | 89% | 79% | 0.141 |
| futbolfantasy ←read | 319 | 40% | 39% | 0.062 |
| analitica — named, no number | 34 | — | 91% | — |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 658 | 86% | 13% | 0.685 |
| futbolfantasy ←read | 2197 | 40% | 6% | 0.258 |
| analitica — named, no number | 85 | — | 2% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 76 |
| within 10 points of 50%, on appearances | 552 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._
