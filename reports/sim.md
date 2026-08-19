# The simulation — 2026-08-19T1904Z

## Now

**Locks in 21h** · squad 226.93M · cash 8.92M · total 235.84M

play 4-5-1 · finish 1.43 · win 64% · season 1,599–1,964

_market **54th percentile** · an ordinary week · better in 46% of weeks_

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season |
|---|---|--:|--:|---|--:|--:|
| **XI — no change, you are fielding the best eleven** | | | | | | |
| **Your eleven — play 4-5-1** | | | **46.56** | vs SusoGattuso **44.36** | | **+2.21** |
| **SELL — never start** | | | | | | |
| Igor Zubeldia | DEF | 71% | 2.34 | yours | +10.30M | — |
| Beñat Turrientes | MED | 35% | 1.11 | yours | +6.96M | — |
| **BUY — with the proceeds** | | | | | | |
| Matias Dituro | POR | 99% | 5.65 | Albert Laporta | -0.62M | +0 |
| Yuri Berchiche | DEF | 92% | 4.79 | SusoGattuso | -2.00M | +81 |
| Ferran Jutgla | DEL | 97% | 4.13 | Albert Laporta | +0.79M | +58 |
| Antonio Blanco | MED | 99% | 4.11 | BurtonGM89 | -4.49M | +20 |
| Cesar Tarrega | DEF | 99% | 3.97 | SusoGattuso | +1.16M | +52 |
| Aitor Paredes | DEF | 80% | 3.92 | BurtonGM89 | +5.02M | +47 |
| Isi Palazon | DEL | 82% | 3.85 | SusoGattuso | -2.89M | +41 |
| Marc Roca | MED | 91% | 3.83 | Albert Laporta | +4.89M | +12 |
| Izan Merino | MED | 97% | 3.23 | SusoGattuso | +0.94M | +1 |
| Mario Martin | MED | 97% | 3.16 | SusoGattuso | +6.30M | -1 |
| Juan Foyth | DEF | 80% | 3.15 | Albert Laporta | -5.28M | +19 |
| Carlos Puga | DEF | 92% | 3.01 | BurtonGM89 | +1.86M | +14 |
| **SAVE — better than yours, out of reach** | | | | | | |
| Joan Garcia | POR | 99% | 8.24 | free agent | 43.83M short | +82 if you could |
| Dean Huijsen | DEF | 97% | 5.53 | BurtonGM89 | 10.79M short | +107 if you could |
| Santiago Mouriño | DEF | 97% | 3.75 | Albert Laporta | 14.05M short | +39 if you could |
| Mario Soriano | MED | 99% | 2.99 | free agent | 0.99M short | -2 if you could |

_Read it top to bottom: it is a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. **Start** is one number, futbolfantasy recalibrated against confirmed line-ups and blended with analiticafantasy where it has an opinion, and it is the same figure the forecast multiplies by. **xPts/j** is what he scores a jornada with that already applied. **€** is the cash you END UP with for doing that row, funding included — a SELL row is what it raises, a BUY row is that money minus what he costs — and on a SAVE row it is how far short you are. **Season** is simulated: extra points over the 38 jornadas left, measured in the same seasons with and without the move._

## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 38 players you can buy now | +214 | — |
| Wait for the market | a week of new offers | +203 | 46% |
| Wait for the clauses | 59 players on 24 Aug | +166 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 131 of 573 |
| Tenth percentile of a week's waiting | +3.83 |
| Market model | the market is modelled from 75 offers over 5 cycles, weighted by value^0.15 |
| Locked players who would improve your eleven | 33 |
| Their clauses open | 24 Aug, in about 5 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Lamine Yamal | +7.53 | 16 days |
| Kylian Mbappe | +6.48 | 16 days |
| Zaid Romero | +4.82 | 20 days |
| David Soria | +4.72 | 21 days |


## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 17 | 8.92M | 1,779 | 1,599–1,964 | — |
| SusoGattuso | 23 | ~-24.29M | 1,687 | 1,508–1,867 | 68% |
| BurtonGM89 | 24 | ~-27.93M | 1,512 | 1,341–1,690 | 92% |
| Magic Mike 333 | 11 | ~-817K | 1,364 | 1,178–1,564 | 98% |
| Albert Laporta | 10 | ~9.81M | 1,171 | 1,032–1,320 | 100% |

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
