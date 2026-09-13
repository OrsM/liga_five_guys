# Liga Five Guys — how the numbers are made — 2026-09-13 15:44 UTC




## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 5 is half played — 10 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| Jornada 6 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.761 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 33 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=1.0 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1389 observed matches |
| P(start) fit | P(start) fitted on 652 confirmed starts across 20 team sheets: futbolfantasy recalibrated (logit +0.0 +1.0x), blended 10% with analiticafantasy where it has an opinion (a named starter counts 72%). Brier improves 0.000 on line-ups the fit had not seen |
| win % and finish are single simulated draws | at this trial count the same real inputs have been measured to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |



## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 186 | 12 Sep 22:09 | fetched 27 minutes ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 184 | 13 Sep 15:18 | fetched 27 minutes ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 1,771 | 13 Sep 15:18 | fetched 27 minutes ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 5,604 | 13 Sep 15:18 | fetched 27 minutes ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 124 | 12 Sep 22:09 | fetched 18 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 920 | 13 Sep 15:18 | fetched 27 minutes ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 9,672 | 13 Sep 14:58 | fetched 27 minutes ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 13,084 | 13 Sep 15:18 | fetched 27 minutes ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 3,320 | 13 Sep 15:18 | fetched 11 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 2,415 | 13 Sep 15:18 | fetched 11 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 143,872 | 13 Sep 15:18 | fetched 47 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 144,387 | 13 Sep 15:18 | fetched 27 minutes ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 71,060 | 13 Sep 15:18 | fetched 11 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 723 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 1,419 | 13 Sep 04:35 | fetched 27 minutes ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 155,159 | 13 Sep 15:18 | fetched 11 hours ago |

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
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 1389 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±16% of a rate across the squads | derived, not fitted |

### How to read the tables

**The ladder** — one table, read top to bottom: a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. `Start` is the probable-XI read, recalibrated and blended, the same figure the forecast multiplies by. `xPts/j` already has that applied. `€` on a KEEP or SELL row is what it raises; on a SAVE row, how far short you are. On a BUY or RAID row it is his market price, plus (on a RAID) the extra premium a clause forces above it — his real worth and what taking him costs, not a net cash figure: a raid can leave you holding more cash than it spent, and reading that surplus as the price would say the opposite of what happened. `Season` is simulated: extra points over the jornadas left, the same seasons with and without the move — on a KEEP or SELL row, "without" is his best REAL replacement, not nothing, so a negative number there can still mean keep him: his own best alternative costs more than he does, not that he scores less than zero. `pts/M€` is Season per million actually spent — how CHEAPLY a gain arrived, not how big it is, so read Season first: there are only eleven starting shirts, and a tiny gain at a tiny price can still carry a flattering rate. A dash means the move is net cash-neutral-or-positive (nothing to divide by) or, on a SAVE row, a shortfall nobody can act on yet. `Where` names who holds him, and on a payable clause also the rival closest to affording it — a first name and `today`/`~Nd`, an ESTIMATE off their reconstructed balance, the app's daily allowance, and how fast that manager has actually raised money this season, never a prediction he actually wants the player.

**The league table** — `Pts` is the real league total today. `Season` is the simulated total and its 10-90 band — a mean with no band beside it reads as a prediction it is not. `P(above)` is how often the simulation has you finish above them.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 59 |
| Predicted, total | 202 pts |
| Actual, total | 264 pts |
| **Mean absolute error (per match played)** | **3.0 pts** |
| Pairs predating the fixture term | 8 of 59 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

_24 of 59 intervals overpredicted, 35 underpredicted (mean signed error -1.1 pts) — the "Biggest miss" table below is the tail, not the whole picture._

_vs. a trivial guess (everyone scores the sample's own mean, 4.5 pts/match, no player identity at all): ours 3.01 MAE, that guess 3.17 MAE — does not clearly beat it yet (90% CI on the gap: -0.90 to +0.59 pts, straddles zero)._

_Not enough data yet to break out by scoring band (needs 20+ per bucket) — will start showing once more jornadas have locked._

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 27 | 3.4 | 4.9 | -1.5 |
| neutral | 11 | 4.0 | 4.8 | -0.8 |
| easier | 13 | 3.9 | 4.4 | -0.5 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Omar El Hilali | 2.6 | 14 | -11.4 |
| Iñigo Vicente | 3.0 | 12 | -9.0 |
| Beñat Turrientes | 1.1 | 10 | -8.9 |
| Orri Steinn Óskarsson | 1.7 | 10 | -8.3 |
| Íñigo Ruiz de Galarreta | 3.8 | 12 | -8.2 |

### Season-long drift

**Fit from real data this run: 1.00** (h3 wasn't more variable than h1 (0.183 vs 0.191, rate_rel-normalised) — no evidence DRIFT_FRAC should move from 1.00).

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 990 confirmed, 44 locked round(s) | | | | |
| analitica | 766 | 81% | 67% | 0.198 |
| futbolfantasy ←read | 2295 | 35% | 35% | 0.069 |
| our forecast | 70 | 69% | 79% | 0.086 |
| analitica — named, no number | 96 | — | 53% | — |

| **starts, same population AND same instances as our forecast only** — the real fair comparison (n=70 claims) | | | | |
| analitica | 43 | 93% | 81% | 0.132 |
| futbolfantasy ←read | 68 | 70% | 79% | 0.081 |
| our forecast | 70 | 69% | 79% | 0.086 |
_vs analitica: 90% CI on the Brier gap -0.134 to +0.025 — no significant difference._
_vs futbolfantasy: 90% CI on the Brier gap -0.033 to +0.031 — no significant difference._

_Every Brier above excludes claims within 10 points of 50% — a source's hardest, most genuinely uncertain calls, which never enter any number shown here._

| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 2564 | 80% | 20% | 0.578 |
| futbolfantasy ←read | 8418 | 35% | 11% | 0.230 |
| our forecast | 335 | 63% | 18% | 0.401 |
| analitica — named, no number | 685 | — | 9% | — |
| futbolfantasy — named, no number | 3 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 477 |
| jornada 1 — its opener kicked off before this repo saw a kickoff for it, so there is no honest cutoff | all |
| within 10 points of 50%, on appearances | 1656 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._

_Our forecast vs. trivial baselines, n=73 — a DIFFERENT sample than the tables above (this one joins the rate and start sides on jornada via golden_rows(), no 10-point undecided-band exclusion): ours 0.093, a flat 50% guess 0.250, a constant 68% guess 0.186 (Brier, lower is better) — beats both, adding real information._
