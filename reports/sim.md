# The simulation — 2026-08-19T2220Z

## Now

**Locks in 21h** · squad 226.88M · cash 8.91M · total 235.79M

play 4-5-1 · finish 1.24 · win 80% · season 1,610–1,981

_market **31st percentile** · an ordinary week · better in 69% of weeks_

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season |
|---|---|--:|--:|---|--:|--:|
| **XI — no change, you are fielding the best eleven** | | | | | | |
| **Your eleven — play 4-5-1** | | | **46.56** | vs SusoGattuso **42.17** | | **+4.39** |
| **SELL — never start** | | | | | | |
| Igor Zubeldia | DEF | 71% | 2.34 | yours | +10.35M | — |
| Beñat Turrientes | MED | 35% | 1.11 | yours | +6.84M | — |
| **BUY — with the proceeds** | | | | | | |
| David Soria | POR | 99% | 7.33 | free agent | -4.29M | +46 |
| Yuri Berchiche | DEF | 92% | 4.79 | SusoGattuso | -1.95M | +81 |
| Antonio Blanco | MED | 99% | 4.11 | BurtonGM89 | -7.94M | +23 |
| Cesar Tarrega | DEF | 99% | 3.97 | SusoGattuso | -2.30M | +53 |
| Aitor Paredes | DEF | 80% | 3.92 | BurtonGM89 | +1.56M | +50 |
| Izan Merino | MED | 97% | 3.23 | SusoGattuso | +0.83M | +1 |
| Carlos Puga | DEF | 92% | 3.01 | BurtonGM89 | +1.74M | +17 |
| Sergio Canales | MED | 97% | 2.76 | free agent | -7.81M | -2 |
| **SAVE — better than yours, out of reach** | | | | | | |
| Marko Dmitrovic | POR | 97% | 5.44 | BurtonGM89 | 12.55M short | -2 if you could |
| Pierre-Emerick Aubameyang | DEL | 97% | 3.11 | free agent | 10.78M short | +17 if you could |

_Read it top to bottom: it is a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. **Start** is one number, futbolfantasy recalibrated against confirmed line-ups and blended with analiticafantasy where it has an opinion, and it is the same figure the forecast multiplies by. **xPts/j** is what he scores a jornada with that already applied. **€** is the cash you END UP with for doing that row, funding included — a SELL row is what it raises, a BUY row is that money minus what he costs — and on a SAVE row it is how far short you are. **Season** is simulated: extra points over the 38 jornadas left, measured in the same seasons with and without the move._

## Act now or wait — the workings

| Route | What it offers | Season pts | Beats acting today |
|---|---|--:|--:|
| **Act today** | 27 players you can buy now | +180 | — |
| Wait for the market | a week of new offers | +204 | 69% |
| Wait for the clauses | 58 players on 24 Aug | +167 | — |

| The workings | |
|---|--:|
| Unowned players who would improve your eleven | 135 of 580 |
| Tenth percentile of a week's waiting | +3.90 |
| Market model | the market is modelled from 89 offers over 5 cycles, weighted by value^0.15 |
| Locked players who would improve your eleven | 32 |
| Their clauses open | 24 Aug, in about 5 days |

| Nobody is offering | Would add | Likely wait |
|---|--:|--:|
| Lamine Yamal | +7.53 | 16 days |
| Kylian Mbappe | +6.48 | 16 days |
| Joan Garcia | +5.64 | 18 days |
| Zaid Romero | +4.82 | 20 days |


## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 31 | 8.91M | 1,795 | 1,610–1,981 | — |
| SusoGattuso | 26 | ~2.13M | 1,606 | 1,422–1,783 | 83% |
| BurtonGM89 | 28 | ~15.88M | 1,455 | 1,285–1,623 | 96% |
| Magic Mike 333 | 15 | ~-817K | 1,369 | 1,177–1,565 | 98% |
| Albert Laporta | 10 | ~9.81M | 1,171 | 1,025–1,323 | 100% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 1 is half played — 10 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| P(start) is today's, held flat over every remaining jornada | nothing here knows who will be injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently | two defenders of one club share a clean sheet, so a concentrated squad has more variance than this shows |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| Shape prior | shape from the seed prior (128 observed, 200 needed) |
| P(start) fit | P(start) fitted on 300 confirmed starts across 10 team sheets: futbolfantasy recalibrated (logit -0.5 +3.4x), blended 70% with analiticafantasy where it has an opinion (a named starter counts 92%). Brier improves 0.023 on line-ups the fit had not seen |
