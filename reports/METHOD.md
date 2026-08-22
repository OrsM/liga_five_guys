# Liga Five Guys — how the numbers are made — 2026-08-22 15:03 UTC




## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 25 players you can buy now | +118 | — |
| Wait for the market | a week of new offers | +270 | 62% |
| Wait for the clauses | 57 players on 24 Aug | +216 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 195 of 589 |
| Tenth percentile of a week's waiting | +5.98 |
| Market model | the market is modelled from 129 offers over 8 cycles, weighted by value^0.40, and only just — value^0.15 fits within 4.9% of it, so read the exponent as roughly this, not exactly this |
| Locked players who would improve your eleven | 43 |
| Their clauses open | 24 Aug, in about 2 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Lamine Yamal | +7.37 | 6 days |
| Joan Garcia | +6.61 | 8 days |
| Kylian Mbappe | +5.98 | 6 days |
| Zaid Romero | +5.84 | 11 days |


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
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 82 | 21 Aug 21:27 | fetched 3 minutes ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 75 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 572 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 2,426 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 68 | 21 Aug 21:27 | fetched 18 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 375 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 1,176 | 21 Aug 21:27 | fetched 3 minutes ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 5,386 | 22 Aug 15:01 | fetched 3 minutes ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 1,140 | 22 Aug 15:01 | fetched 8 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 1,198 | 22 Aug 15:01 | fetched 8 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 75,675 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 71,784 | 22 Aug 15:01 | fetched 3 minutes ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 29,640 | 22 Aug 15:01 | fetched 8 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 663 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 193 | 22 Aug 07:13 | fetched 3 minutes ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 19,394 | 22 Aug 15:01 | fetched 8 hours ago |

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


_What the one table on the board was built from: the eleven it assumes you field, what a man on today's slate should cost, and the two ways any of it can be wrong about a player._

## 2. What to bid

| Player — 13 on offer | Pos | Bid | XI | Competition | Note |
|---|---|--:|--:|---|---|
| Jonathan Dubasin | del | 13.65M–16.36M | 40%/— | (1 broke) |  |
| Miguel Rodriguez | del | 2.68M–3.21M | 10%/— | none |  |
| Fernando Calero | def | 2.42M–2.90M | 0%/— | none |  |
| Ezequiel Ponce | del | 1.70M–2.04M | 30%/— | none |  |
| Daniel Raba | del | 1.39M–1.67M | 0%/— | none |  |
| Randy Nteka | del | 1.27M–1.53M | 30%/— | Magic +0.4 |  |
| Luis Miguel Ramis | ent | 1.22M–1.46M | !15%/— | none |  |
| Manolo Gonzalez | ent | 971K–1.16M | !15%/— | none |  |
| Arsen Zakharyan | med | 898K–1.08M | 30%/— | Albert +0.2 |  |
| Nico Serrano | del | 783K–938K | 0%/— | none |  |
| Alejandro Iturbe | por | 734K–880K | 10%/— | none |  |
| Marcao | def | 583K–699K | !15%/— | Albert +0.2 |  |
| Luiz Junior | por | — | 60%/100% | (1 broke) |  |

| Column | What it is |
|---|---|
| Bid | what it costs to win him — never whether he is worth winning, which is the one table on the board. A purchase is closer to a loan than a spend: the value comes back on sale, give or take 12%, so a bid within a few percent is not a decision |
| Bid, how it is priced | the floor plus what this league has actually paid over it: median +1.5%, -0.3% to +21.6% (n=39). 17 of those 39 went at the floor itself, so the minimum is not a number known to lose. The range is what has happened, not a chance of winning — and every one of them is a bid that won |
| XI | FF's probable-eleven percentage — the one the forecast uses — then AF's read of the same eleven. Printed, never blended: two sources that disagree is the signal, and the reason to open the app before bidding |
| XI, the marks | **FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both. |
| Competition | demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first. `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. Manager by manager in `reports/rivals.md` |

**⚠ 12 bids at 28.30M is more than the 19.38M you hold.** Each row above is priced as though it were your only purchase, and the app settles them together — take them best rate first: Jonathan Dubasin, Miguel Rodriguez, Fernando Calero, Ezequiel Ponce, Daniel Raba, Randy Nteka, Luis Miguel Ramis, Manolo Gonzalez, Arsen Zakharyan, Nico Serrano, Alejandro Iturbe, Marcao.

| Already owned, so not a purchase | Held by |
|---|---|
| santiago mouriño | Albert Laporta |
| carlos espi | SusoGattuso |
| javi rueda | BurtonGM89 |
| sergio canales | BurtonGM89 |
| carlos puga | BurtonGM89 |
| justin de haas | BurtonGM89 |
| leandro cabrera | Albert Laporta |
| ionut radu | you |
| diego javier llorente | Albert Laporta |
| yuri berchiche | SusoGattuso |
| antonio blanco | BurtonGM89 |
| abde ezzalzouli | Albert Laporta |

## 3. Exceptions

### Fitness

| Fitness | Players |
|---|---|
| flagged | **0** of 12 |
| app disagrees, FF's panel does not | **0** |
| listed, no flag | 12 — Carl Starfelt, Igor Zubeldia, Ionut Radu, Iñigo Ruiz de Galarreta, Iñigo Vicente, Jon Moncayola, Lucien Agoume, Marcos Alonso, Omar El Hilali, Pablo Fornals, Pepelu, Robin Le Normand |
| no entry on their team page — unknown, not fit | 0 |

_FF's read is from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page; `Tocado` — a knock the site still lists as available — is folded into doubt. No entry is an absence of evidence, not evidence of fitness. 'App' is the game's own operator-stated availability, shown whenever it differs from FF's read._

### Starting

| Your XI under 60% | Reading |
|---|--:|
| Iñigo Ruiz de Galarreta | 0% |

| | Player | The two sources disagree |
|---|---|---|
| XI | Robin Le Normand | futbolfantasy 70%, analitica 50% |

_Both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` not on the page at all (assumed 15%). Threshold is `min_start` in `inputs/league.ini`._

---

## Notes

| | |
|---|---|
| Players tracked | 658, 497 with a probable-XI reading |
| xPts/j | expected points per jornada = shrunk pts/match (K=8, 2025-26 + 2026-27) × fixture × P(start), from `ffcore/score.py` — the same scorer rivals.py uses |
| Zeroed | injured, suspended, unavailable; a doubt is halved |
| Fixture term | ±12% across the opponents ranked by nothing: no fixture is known for anyone in your squad, plus ±4% for home advantage — both widths guesses, unfitted, and small enough that a wrong one costs a fraction of a point |

_Generated 2026-08-22 15:03 UTC._
