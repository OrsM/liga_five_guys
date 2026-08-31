# Liga Five Guys — how the numbers are made — 2026-08-31 22:44 UTC




## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 56 players you can buy now | +217 | — |
| Wait for the market | a week of new offers | +114 | 0% |
| Wait for the clauses | 20 players on 02 Sep | +144 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 144 of 600 |
| Tenth percentile of a week's waiting | +0.86 |
| Market model | the market is modelled from 321 offers over 17 cycles, weighted by value^0.25, and only just — value^0.20 fits within 3.3% of it, so read the exponent as roughly this, not exactly this |
| Locked players who would improve your eleven | 15 |
| Their clauses open | 02 Sep, in about 2 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Kylian Mbappe | +6.59 | 9 days |
| Joan Garcia | +5.48 | 11 days |
| Lamine Yamal | +5.17 | 9 days |
| Nicolas Pepe | +4.77 | 11 days |


## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 3 is half played — 16 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.002 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| "X can pay in ~N days" is an estimate, and says what it assumes | it is their reconstructed balance (`~`: the app states `teamMoney` for your account alone, so a rival's can be a whole unseen sale wrong), plus the 100K daily allowance, plus the rate that manager has ACTUALLY raised money at across the ledger — measured over the ledger's own 20 days: Albert 14.3M/day off 17 sales, BurtonGM89 14.2M/day off 19 sales, Magic 1.0M/day off 2 sales, SusoGattuso 5.8M/day off 9 sales. They differ by an order of magnitude, which is the whole reason this is per rival and not one number. Capped at what his squad is worth, since nobody can sell more than he holds. What it does NOT model is whether he WANTS the player, only whether he could pay: read it as how long the door stays open, never as a prediction that he walks through it. An allowance-only version was tried first and rejected as unactionable — it put the manager who raised 86.9M in six sales last week 450 days away from affording anything |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread rests on one hand-picked constant (DRIFT_FRAC=1.0), not a fit | two real anchors on this repo's own data disagree on the exact magnitude (weak jornada-1-vs-final correlation argues wider, strong season-to-season correlation argues narrower), but every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless — that floor doesn't need the two anchors resolved. This caveat used to say to tighten it once the Forecast vs actual table above had enough rows — it has them now, and the check (2026-08-31) found that table can never grade this constant at ANY row count: every pair in it is one jornada out, and this constant only acts across longer horizons. It is not waiting on more data, it is waiting on a different measurement |
| Shape prior | shape from 823 observed matches |
| P(start) fit | P(start) fitted on 620 confirmed starts across 20 team sheets: futbolfantasy recalibrated (logit +0.0 +1.4x), blended 20% with analiticafantasy where it has an opinion (a named starter counts 77%). Brier improves 0.002 on line-ups the fit had not seen |


## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 127 | 31 Aug 22:15 | fetched 1 minute ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 127 | 31 Aug 22:44 | fetched 1 minute ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 1,144 | 31 Aug 22:44 | fetched 1 minute ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 3,965 | 31 Aug 22:44 | fetched 1 minute ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 100 | 31 Aug 22:15 | fetched 29 minutes ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 635 | 31 Aug 22:44 | fetched 1 minute ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 5,178 | 31 Aug 22:15 | fetched 1 minute ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 9,014 | 31 Aug 22:44 | fetched 1 minute ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 2,180 | 31 Aug 22:44 | fetched 17 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 1,854 | 31 Aug 22:44 | fetched 17 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 108,803 | 31 Aug 22:44 | fetched 29 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 106,378 | 31 Aug 22:44 | fetched 1 minute ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 49,400 | 31 Aug 22:44 | fetched 17 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 690 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 852 | 31 Aug 05:23 | fetched 1 minute ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 59,425 | 31 Aug 22:44 | fetched 17 hours ago |

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
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 823 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±16% of a rate across the squads | derived, not fitted |

### How to read the tables

**Field these eleven** — `pts/m` is points per match, last season shrunk toward the average and blended with this season as it accrues (`~` = no record at all, the baseline is assumed). `Fix` is how much the next opponent moves it (`=` a median team, `—` no fixture known). `FF`/`AF` are separate, never blended, because neither has been checked against a played jornada yet and a disagreement is worth more than an average. `xPts/j` = pts/m × Fix × FF, and uses FF only. `⚠` on a name means the Fitness or Starting section has something on him. On a `+SLOT` row, xPts/j is the change to the WHOLE eleven if you sign him and re-pick the shape — his own score, fixture excluded, since you'd own him for months, not one round.

**What to bid** — `Bid` is what it costs to win him, never whether he is worth winning (that is the eleven table). A purchase is closer to a loan than a spend: the value comes back on sale, give or take a tenth, so a bid within a few percent is not a decision. It is priced as the floor plus what this league has actually paid over it — the range is what has happened, not a chance of winning, and every one of them is a bid that won. `XI` is two readings, printed side by side. **FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both. `Competition` is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` means their cash is unknown (treat as live), `(n broke)` means they want him but cannot pay the floor.

**Fitness** — FF's read is from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page; `Tocado` — a knock the site still lists as available — is folded into doubt. No entry is an absence of evidence, not evidence of fitness. 'App' is the game's own operator-stated availability, shown only when it differs from FF's read.

**Starting** — both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` means not on the page at all (assumed 15%). The under-threshold cutoff is `min_start` in `inputs/league.ini`.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 44 |
| Predicted, total | 144 pts |
| Actual, total | 165 pts |
| **Mean absolute error** | **3.1 pts per player-match** |
| Pairs predating the fixture term | 7 of 44 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| under 2 | 13 | 1.6 | 2.4 |
| 2–3 | 8 | 2.6 | 5.8 |
| 3–4 | 13 | 3.5 | 4.1 |
| 4+ | 10 | 5.7 | 3.5 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 21 | 3.3 | 2.4 | +0.8 |
| neutral | 8 | 4.4 | 5.0 | -0.6 |
| easier | 8 | 3.6 | 6.2 | -2.6 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Iñigo Vicente | 3.0 | 12 | -9.0 |
| Orri Steinn Óskarsson | 1.7 | 10 | -8.3 |
| Íñigo Ruiz de Galarreta | 3.8 | 12 | -8.2 |
| Dani Martínez | 0.5 | 8 | -7.5 |
| Rubén García | 3.1 | 10 | -6.9 |

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 616 confirmed, 3 locked round(s) | | | | |
| analitica | 390 | 85% | 73% | 0.181 |
| futbolfantasy ←read | 1318 | 39% | 39% | 0.088 |
| analitica — named, no number | 69 | — | 71% | — |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 1793 | 81% | 20% | 0.593 |
| futbolfantasy ←read | 6689 | 38% | 9% | 0.248 |
| analitica — named, no number | 635 | — | 6% | — |
| futbolfantasy — named, no number | 2 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 275 |
| within 10 points of 50%, on appearances | 1499 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._
