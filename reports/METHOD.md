# Liga Five Guys — how the numbers are made — 2026-08-18 23:29 UTC

Everything about HOW, so the report can be the numbers. The fits, the estimates, and every way each one is known to be wrong.


## Act now or wait — the workings

| Route | What it offers | Season pts |
|---|---|--:|
| **Act today** | 38 players you can buy now | +209 |
| Wait for the market | a week of new offers | +198 |
| Wait for the clauses | 62 players on 24 Aug | +160 |

_Season points, so this can be compared with the table below rather than sitting in its own unit. Waiting pays for the delay: a jornada of the best thing you can buy today is forgone before the better one arrives. These are estimates from a rate; the table's are simulated._

**Not for sale, and you cannot ask.** The app deals about a dozen players a cycle out of five hundred, so a man you want is not something you can go and buy — being unowned is not being available:

| Player | Would add | Likely wait to be offered |
|---|--:|--:|
| Lamine Yamal | +7.37 | 16 days |
| Kylian Mbappe | +6.41 | 16 days |
| Jan Oblak | +4.91 | 18 days |
| Zaid Romero | +4.58 | 20 days |

_The free market is simulated rather than guessed at: the market is modelled from 72 offers over 5 cycles, weighted by value^0.15. **111 of the 572 unowned players** would improve your eleven, and a week of offers beats the best thing you can buy today **46% of the time** — even the tenth percentile of waiting (+3.73) clears it. Spending now buys the worse of two options and gives up the choice._
_**31 locked players would improve your eleven** and their clauses open on 24 Aug, in about 6 days. Waiting scores ZERO in the table above — not because it is worthless but because nothing there can price a market it has not seen, so every move with a positive number beats it by construction. That is the bias to hold in mind when the ranking asks you to spend the balance; the **Left** column is what buys the choice._

## What the simulation cannot see

_shape from the seed prior (96 observed, 200 needed)._

_P(start) fitted on 240 confirmed starts across 8 team sheets: futbolfantasy recalibrated (logit -0.5 +5.8x), blended 80% with analiticafantasy where it has an opinion (a named starter counts 94%). Brier improves 0.032 on line-ups the fit had not seen._

- **Jornada 1 is half played.** 8 clubs are done and their points are already in the `now` column, so the simulation only plays the rest of the round. It still re-picks an eleven that is in fact already locked.
- **A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000.** A clause runs a median 1.52x market value in this league and the app only ever pays the value back, so the premium is gone for good. It is charged against the move rather than ignored — but the price is measured off what more money would actually buy you today, and on most days that is very little.
- **P(start) is today's, held flat over every remaining jornada.** Nothing here knows who will be injured in March.
- **Rivals never transfer.** A steal that guts a squad assumes its manager does not simply buy someone back.
- **Teammates score independently.** Two defenders of one club share a clean sheet, so a concentrated squad really has more variance than this shows.
- **Cash scores zero.** Nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good.


### The model, as configured right now

`xPts/j = shrunk points-per-match x fixture x P(start)`. What each term means, why it is shaped that way, and what it deliberately ignores is in the README — this table is only what the code is currently set to, read from the code so it cannot drift.

| Term | Setting |
|---|---|
| Shrinkage | K = 8 matches, applied twice (last season toward the positional prior, then this season toward that) |
| Fixture band | ±12%, plus 4% at home — a RANK, not a ratio |
| Team strength | **Club Elo rating**, a result-based rating with no transfer fees in it |
| P(start) read from | `futbolfantasy` |
| Fixture applies to | fielding only — never to a buy, a sale, or the line |

**Both fixture numbers are guesses, not fits.** The tables below are what will eventually settle them.

### Forecast vs actual — last 21 days

**2 player-intervals** (2026-27): predicted **5** pts total, actual **14**. Mean absolute error **4.3 pts per player-match** — read every xPts/j in this report as ± that, at least.

Only predictions logged **before** each interval are scored; hindsight is excluded by construction. Sample is your own squad, so it grows ~15 pairs a jornada.

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| 2–3 | 2 | 2.7 | 7.0 |

**Is the fixture term earning its place?** It moves a forecast by up to ±12% and was never fitted, so this is the table that decides whether to keep it, widen it, or delete it.

| Next fixture | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 1 | 2.6 | 6.0 | -3.4 |
| easier | 1 | 2.8 | 8.0 | -5.2 |

_Per player-match. A positive error against an **easier** fixture together with a negative one against a **harder** fixture means the band is too wide; the reverse means too narrow; both near zero means it is roughly right. Judge nothing on a bucket with a single-digit n._

Biggest misses (forecast − actual):

- **Omar El Hilali** — forecast 2.8, actual 8 (-5.2)
- **Iñigo Vicente** — forecast 2.6, actual 6 (-3.4)

### Who to believe about the eleven

**88 confirmed starters** across 1 locked round(s), off the match pages. A substitute is a MISS here — this is the question both sources are answering. The claim scored is the last one published before the round's first kickoff, because that is when the lineup locked and later news could not have been acted on.

| Source | Calls | Mean claim | Started | Brier |
|---|--:|--:|--:|--:|
| analitica | 26 | 88% | 85% | 0.111 |
| futbolfantasy ←read | 176 | 42% | 41% | 0.035 |

_**Brier** is the mean squared error of the probability: lower is better, 0.25 is a coin flip, and it punishes confidence more than caution. A source whose mean claim sits far from its start rate is miscalibrated even if it ranks players well._

- **analitica** — 31 calls published with no number on them, 100% started

_40 claim(s) sat within 10 points of 50%: not a call either way, so not graded._

_Read the gap between the sources, not the level. The clubs playing the round's opening matches have their elevens CONFIRMED by the time it locks, and both sources copy them, so their share of the table is scored on published fact rather than on a forecast. Whichever source publishes more of those looks better than it forecasts._

The wider but blunter sample, kept because it reaches back to before the match pages were collected:

Graded on **appearances**, not starts: a 20-minute substitute counts. That flatters both sources by the same amount, so the comparison holds even though the level does not. Only claims logged before the interval opened are scored.

| Source | Calls | Mean claim | Appeared | Brier |
|---|--:|--:|--:|--:|
| analitica | 239 | 86% | 17% | 0.665 |
| futbolfantasy ←read | 856 | 40% | 7% | 0.261 |
Calls published with no number on them, which can only be graded as a hit rate:

- **analitica** — 39 named starters, 0% appeared

_220 claim(s) sat within 10 points of 50% and are not graded: that is not a call either way._

**The gate.** Once a source has a few hundred graded calls, whichever has the lower Brier **on the starts table above** earns `LINEUP_SOURCE` in ffcore/tidy.py — a one-line change, and the only thing that should ever move it. Appearances break a tie, never the other way round: they are the question nobody asked. Until then nothing is blended, because a weight fitted on one jornada is a guess wearing a decimal point.


_What the one table in [REPORT.md](REPORT.md) was built from: the eleven it assumes you field, what a man on today's slate should cost, and the two ways any of it can be wrong about a player._

## 2. What to bid

**12 on offer.**

| Player | Pos | Bid | XI | Competition | Note |
|---|---|--:|--:|---|---|
| Javi Puado | med | — | 0%/— | none |  |
| Matias Vecino | med | — | 20%/— | none |  |
| Abiel Osorio | del | — | !15%/— | none |  |
| Peter Gulacsi | por | — | 40%/— | none |  |
| Trent Alexander-Arnold | def | — | 50%/— | (2 broke) |  |
| Mario Soriano | med | — | 100%/— | (4 broke) |  |
| Iñigo Perez | ent | — | !15%/— | none |  |
| Nico Guillen | med | — | 50%/— | (3 broke) |  |
| Joan Garcia | por | — | 80%/100% | (4 broke) |  |
| Manel Usedo | med | — | !15%/— | none |  |
| Iago Aspas | del | — | 40%/— | none |  |
| Peque | del | — | 50%/— | none |  |

**Bid** is what it costs to win him, and nothing here says whether he is worth winning — that is the one table in [REPORT.md](REPORT.md), which prices him at a clause because a clause cannot be refused. A purchase is closer to a loan than a spend: the value comes back when you sell, give or take 12%, which on a large player is bigger than the premium and the drift put together. That swing is a coin flip, so a bid within a few percent is not a decision.

**XI** is FF's probable-eleven percentage, which is the one the forecast uses, and AF's read of the same eleven beside it — printed, never blended. Two sources that disagree is the signal, and it is the reason to open the app before bidding. **FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both.

Competition is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. The full manager-by-manager matrix is in `reports/rivals.md`.

Bid is the floor plus what this league has actually paid over it: median +1.4%, -0.3% to +635.3% (n=32). 14 of those 32 went at the floor itself, so the minimum is not a number known to lose. A dozen deals is not a distribution — the range is what has happened, not a chance of winning, and every one of them is a bid that won.

Already owned, so not a purchase: abde ezzalzouli (Albert Laporta), aitor paredes (BurtonGM89), antonio blanco (BurtonGM89), arda guler (Albert Laporta), ayoze perez (Albert Laporta), beñat turrientes (you), carlos espi (SusoGattuso), carlos puga (BurtonGM89), cesar tarrega (SusoGattuso), dani lorenzo (you), dean huijsen (BurtonGM89), denis suarez (BurtonGM89), ferran jutgla (Albert Laporta), ilaix moriba (Albert Laporta), isi palazon (SusoGattuso), izan merino (SusoGattuso), johnny cardoso (SusoGattuso), juan foyth (Albert Laporta), karl etta eyong (BurtonGM89), leandro cabrera (Albert Laporta), marc roca (Albert Laporta), mario martin (SusoGattuso), matias dituro (Albert Laporta), quilindschy hartman (BurtonGM89), santiago mouriño (Albert Laporta), yuri berchiche (SusoGattuso).

## 3. Exceptions

_The two ways every number above can be wrong about a player: he is not fit, or the two probable-XI sources do not agree that he plays. Neither prices anything, so neither is a decision — both are prompts to open the app._

### Fitness

**Nobody in your squad is flagged.** All 14 players with an entry on their team page read as available.

_Listed with no flag (14): Beñat Turrientes, Carl Starfelt, Dani Lorenzo, Igor Zubeldia, Ionut Radu, Iñigo Ruiz de Galarreta, Iñigo Vicente, Jon Moncayola, Lucien Agoume, Marcos Alonso, Omar El Hilali, Pablo Fornals, Pepelu, Robin Le Normand._

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page. A knock the site still lists as available (`Tocado`) is folded into doubt._

### Starting

**1 of your marked XI are under 60%:** Robin Le Normand (60%).

_Both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` not on the page at all (assumed 15%). Threshold is `min_start` in `inputs/league.ini`._

---

## Notes

_652 players tracked, 499 with a probable-XI reading._

_xPts/j — expected points per jornada = shrunk pts/match (K=8, 2025-26) × fixture × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Injured, suspended and unavailable score zero; a doubt is halved. The fixture term is a ±12% band across the opponents ranked by Club Elo rating, plus ±4% for home advantage; both widths are guesses, unfitted because nothing has been played, and small enough that a wrong one costs a fraction of a point._

_Generated 2026-08-18 23:06 UTC._
