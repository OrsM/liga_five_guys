# Liga Five Guys — how the numbers are made — 2026-08-19 09:06 UTC




## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 39 players you can buy now | +213 | — |
| Wait for the market | a week of new offers | +202 | 48% |
| Wait for the clauses | 62 players on 24 Aug | +160 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 110 of 572 |
| Tenth percentile of a week's waiting | +3.95 |
| Market model | the market is modelled from 73 offers over 5 cycles, weighted by value^0.15 |
| Locked players who would improve your eleven | 31 |
| Their clauses open | 24 Aug, in about 5 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Lamine Yamal | +6.86 | 16 days |
| Kylian Mbappe | +6.36 | 16 days |
| Jan Oblak | +5.02 | 18 days |
| Zaid Romero | +4.69 | 20 days |


## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 1 is half played — 10 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| P(start) is today's, held flat over every remaining jornada | nothing here knows who will be injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently | two defenders of one club share a clean sheet, so a concentrated squad has more variance than this shows |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| Shape prior | shape from the seed prior (96 observed, 200 needed) |
| P(start) fit | P(start) fitted on 300 confirmed starts across 10 team sheets: futbolfantasy recalibrated (logit -0.5 +3.4x), blended 70% with analiticafantasy where it has an opinion (a named starter counts 92%). Brier improves 0.023 on line-ups the fit had not seen |


## Where the numbers come from

| Table | What it is used for | Fetched from | Rows | Newest row | State |
|---|---|---|--:|---|---|
| api_activity | every transfer, which is what the ledger replays | LaLiga Fantasy API | 910 | 19 Aug 00:29 | ok |
| api_leagues | your cash and the league's id | LaLiga Fantasy API | 15 | 19 Aug 00:29 | ok |
| api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 602 | 19 Aug 00:29 | ok |
| api_players | names for players nobody owns any more | LaLiga Fantasy API | 745 | 19 Aug 00:29 | ok |
| api_teams | all five squads | LaLiga Fantasy API | 1,140 | 19 Aug 00:29 | ok |
| clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run |
| elo | team strength, which ranks the fixture term | api.clubelo.com | 80 | 17 Aug 23:45 | **33 hours stale** |
| fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 352 | 19 Aug 00:29 | ok |
| lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 31,597 | 19 Aug 00:29 | ok |
| market | price, value, position, fitness — every player in the game | futbolfantasy.com | 32,515 | 19 Aug 00:29 | ok |
| matches | fixtures, kickoffs, results | futbolfantasy.com | 6,840 | 19 Aug 00:29 | ok |
| players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 648 | — | rebuilt every run |
| points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 100 | 18 Aug 09:41 | ok |
| starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 3,506 | 19 Aug 00:29 | ok |

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


_What the one table in [REPORT.md](REPORT.md) was built from: the eleven it assumes you field, what a man on today's slate should cost, and the two ways any of it can be wrong about a player._

## 2. What to bid

| Player — 12 on offer | Pos | Bid | XI | Competition | Note |
|---|---|--:|--:|---|---|
| Abiel Osorio | del | — | 0%/— | none |  |
| Mario Soriano | med | — | 80%/100% | (4 broke) |  |
| Javi Puado | med | — | 0%/— | none |  |
| Matias Vecino | med | — | 20%/— | none |  |
| Iago Aspas | del | — | 40%/— | none |  |
| Peque | del | — | 50%/— | none |  |
| Joan Garcia | por | — | 80%/100% | (4 broke) |  |
| Nico Guillen | med | — | 50%/— | (3 broke) |  |
| Iñigo Perez | ent | — | !15%/— | none |  |
| Peter Gulacsi | por | — | 40%/— | none |  |
| Manel Usedo | med | — | !15%/— | none |  |
| Trent Alexander-Arnold | def | — | 50%/— | (2 broke) |  |

| Column | What it is |
|---|---|
| Bid | what it costs to win him — never whether he is worth winning, which is the one table in [REPORT.md](REPORT.md). A purchase is closer to a loan than a spend: the value comes back on sale, give or take 12%, so a bid within a few percent is not a decision |
| Bid, how it is priced | the floor plus what this league has actually paid over it: median +1.4%, -0.3% to +635.3% (n=32). 14 of those 32 went at the floor itself, so the minimum is not a number known to lose. The range is what has happened, not a chance of winning — and every one of them is a bid that won |
| XI | FF's probable-eleven percentage — the one the forecast uses — then AF's read of the same eleven. Printed, never blended: two sources that disagree is the signal, and the reason to open the app before bidding |
| XI, the marks | **FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both. |
| Competition | demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first. `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. Manager by manager in `reports/rivals.md` |

| Already owned, so not a purchase | Held by |
|---|---|
| abde ezzalzouli | Albert Laporta |
| aitor paredes | BurtonGM89 |
| antonio blanco | BurtonGM89 |
| arda guler | Albert Laporta |
| ayoze perez | Albert Laporta |
| beñat turrientes | you |
| carlos espi | SusoGattuso |
| carlos puga | BurtonGM89 |
| cesar tarrega | SusoGattuso |
| dani lorenzo | you |
| dean huijsen | BurtonGM89 |
| denis suarez | BurtonGM89 |
| ferran jutgla | Albert Laporta |
| ilaix moriba | Albert Laporta |
| isi palazon | SusoGattuso |
| izan merino | SusoGattuso |
| johnny cardoso | SusoGattuso |
| juan foyth | Albert Laporta |
| karl etta eyong | BurtonGM89 |
| leandro cabrera | Albert Laporta |
| marc roca | Albert Laporta |
| mario martin | SusoGattuso |
| matias dituro | Albert Laporta |
| omar el hilali | you |
| quilindschy hartman | BurtonGM89 |
| santiago mouriño | Albert Laporta |
| yuri berchiche | SusoGattuso |

## 3. Exceptions

### Fitness

| Fitness | Players |
|---|---|
| flagged | **0** of 14 |
| listed, no flag | 14 — Beñat Turrientes, Carl Starfelt, Dani Lorenzo, Igor Zubeldia, Ionut Radu, Iñigo Ruiz de Galarreta, Iñigo Vicente, Jon Moncayola, Lucien Agoume, Marcos Alonso, Omar El Hilali, Pablo Fornals, Pepelu, Robin Le Normand |
| no entry on their team page — unknown, not fit | 0 |

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page; `Tocado` — a knock the site still lists as available — is folded into doubt. No entry is an absence of evidence, not evidence of fitness._

### Starting

| | Player | The two sources disagree |
|---|---|---|
| XI | Robin Le Normand | futbolfantasy 70%, analitica 50% |

_Both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` not on the page at all (assumed 15%). Threshold is `min_start` in `inputs/league.ini`._

---

## Notes

| | |
|---|---|
| Players tracked | 652, 512 with a probable-XI reading |
| xPts/j | expected points per jornada = shrunk pts/match (K=8, 2025-26) × fixture × P(start), from `ffcore/score.py` — the same scorer rivals.py uses |
| Zeroed | injured, suspended, unavailable; a doubt is halved |
| Fixture term | ±12% across the opponents ranked by Club Elo rating, plus ±4% for home advantage — both widths guesses, unfitted, and small enough that a wrong one costs a fraction of a point |

_Generated 2026-08-19 09:06 UTC._
