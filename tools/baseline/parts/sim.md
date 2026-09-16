# The simulation — 2026-09-16T2115Z

## Now

**Locks in 5h** · squad 286.03M · cash 7.52M · total 293.55M

play 5-4-1 · finish 2.59 · win 26% · season 703–2,555

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Dakonam Djene | DEF | 87% | 3.06 | bench | — | -0 (-93–+74) | +18 | — |
| **TAKE OFF** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.24 | yours | — | +1 (-45–+37) | -2 | — |
| **Your eleven — play 5-4-1** | | | **47.49** | vs BurtonGM89 **41.97** | | **+5.52** | |
| **KEEP — bench** | | | | | | | | |
| Yeray Alvarez | DEF | 25% | 1.01 | yours | — | +1 (-50–+42) | +0 | — |
| Alvaro Mantilla | DEF | 36% | 0.50 | yours | — | -0 (-14–+12) | -18 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | +2 (-74–+61) | +8 | — |
| **SELL — never start** | | | | | | | | |
| Ali Houary | DEL | 31% | 0.86 | yours | +1.06M (bought 1.78M, -0.72M) | +0 (+0–+0) | -38 | — |
| **BUY — free agents — none clear the bar today** | | | | | | | |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Alfonso Herrero | POR | 94% | 4.85 | Magic | 16.49M | +6 (-108–+125) sell Hilali | +20 | — |
| Fran Garcia | DEF | 48% | 3.70 | SusoGattuso | 30.06M | +3 (-128–+104) sell Natan | +15 | — |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 96% | 10.93 | Magic | 84.17M short | +14 (-85–+343) if you could | +122 | 0.2 |
| Pierre-Emerick Aubameyang | DEL | 92% | 6.71 | Albert | 43.50M short | +1 (-84–+162) if you could | +36 | 0.0 |
| Ante Budimir | DEL | 33% | 1.41 | BurtonGM89 | 8.31M short | -4 (-102–+181) if you could | +44 | -0.5 |
| **PASS** | | | | | | | | |
| Alvaro Valles | POR | 76% | 10.40 | free agent | -51.01M | +5 (-108–+162) | +61 | — |
| Facundo Bernal | MED | 63% | 2.80 | free agent | -14.42M | -1 (-46–+61) | +9 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 290 | 7.52M | 1,737 | 703–2,555 | — |
| BurtonGM89 | 241 | ~4.56M | 1,613 | 647–2,442 | 53% |
| SusoGattuso | 210 | ~-15.61M | 1,597 | 610–2,291 | 58% |
| Albert Laporta | 217 | ~36.64M | 1,351 | 535–2,245 | 64% |
| Magic Mike 333 | 226 | ~18.68M | 1,230 | 512–2,045 | 66% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 8 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.203 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 32 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=1.0 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1649 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.104 fitted vs 0.103 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |

## Every listed player, compared

PAR = season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours). Comparable across positions on that basis; a good player can still show a modest PAR if his position is deep league-wide. Parenthesised range is a real simulated band where one was run; a plain figure is the point estimate. Only players ABOVE replacement are listed — see the note below the table for the rest.

| Player | Pos | Price | Season | Next | PAR | pts/€M |
|---|---|---:|---:|---:|---:|---:|
| Alfonso Herrero | POR | 16.49M | 155.6 | 4.9 | 20.2 | 1.23 |
| Fran Garcia | DEF | 30.06M | 100.4 | 3.7 | 15.4 | 0.51 |
| Luismi Cruz | DEL | 9.48M | 79.5 | 4.3 | 1.5 (-31–50) | 0.16 |
| Toni Martinez | DEL | 28.03M | 96.5 | — | 4.0 | 0.14 |
| Raphinha | DEL | 138.15M | 214.3 | 10.9 | 14.3 (-85–343) | 0.10 |
| David Soria | POR | 35.80M | 156.9 | 5.1 | 3.5 (-61–98) | 0.10 |
| Lorenzo Amatucci | MED | 20.08M | 75.3 | 3.9 | 1.9 | 0.10 |
| Alvaro Valles | POR | 51.01M | 196.5 | 10.4 | 4.6 (-108–162) | 0.09 |
| Sergio Herrera | POR | 33.61M | 128.1 | 1.4 | 0.7 (-29–42) | 0.02 |
| Pierre-Emerick Aubameyang | DEL | 97.48M | 128.4 | 6.7 | 1.4 (-84–162) | 0.01 |

_51 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
