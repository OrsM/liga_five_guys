# The simulation — 2026-09-17T1929Z

## Now

**Locks in 29h** · squad 287.86M · cash 7.62M · total 295.48M

play 5-4-1 · finish 2.06 · win 42% · season 1,352–2,167

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.61 | bench | — | -6 (-62–+34) | +3 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.53 | yours | — | -1 (-59–+41) | +1 | — |
| **Your eleven — play 5-4-1** | | | **49.71** | vs BurtonGM89 **44.23** | | **+5.48** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.24 | yours | — | -1 (-54–+34) | +0 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -0 (-16–+13) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -8 (-91–+46) | +9 | — |
| Ali Houary | DEL | 31% | 0.89 | yours | — | +0 (-6–+6) | -41 | — |
| **BUY — free agents — none clear the bar today** | | | | | | | |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.62 | Albert | 15.21M | +13 (-81–+151) sell Starfelt | +43 | 2.4 |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 78% | 7.55 | Magic | 86.17M short | +104 (-36–+388) if you could | +133 | 1.2 |
| Ante Budimir | DEL | 84% | 5.89 | BurtonGM89 | 8.88M short | +29 (-62–+192) if you could | +39 | 3.2 |
| Pierre-Emerick Aubameyang | DEL | 88% | 5.46 | Albert | 44.07M short | +36 (-57–+193) if you could | +40 | 0.8 |
| **PASS** | | | | | | | | |
| Alvaro Valles | POR | 93% | 12.77 | free agent | -51.01M | +28 (-103–+198) | +59 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 291 | 7.62M | 1,741 | 1,352–2,167 | — |
| BurtonGM89 | 241 | ~3.11M | 1,626 | 1,259–2,027 | 60% |
| SusoGattuso | 210 | ~-14.50M | 1,483 | 1,169–1,833 | 73% |
| Albert Laporta | 217 | ~49.47M | 1,415 | 1,060–1,809 | 78% |
| Magic Mike 333 | 233 | ~19.78M | 1,346 | 995–1,740 | 83% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 14 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.671 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 32 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=0.2628726434651191 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1744 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.114 fitted vs 0.113 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |

## Every listed player, compared

PAR = season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours). Comparable across positions on that basis; a good player can still show a modest PAR if his position is deep league-wide. Parenthesised range is a real simulated band where one was run; a plain figure is the point estimate. Only players ABOVE replacement are listed — see the note below the table for the rest.

| Player | Pos | Price | Season | Next | PAR | pts/€M |
|---|---|---:|---:|---:|---:|---:|
| Unai Lopez | MED | 15.21M | 118.8 | — | 43.4 | 2.85 |
| Ramon Terrats | MED | 8.56M | 104.5 | 0.0 | 10.8 (-49–103) | 1.26 |
| Leandro Cabrera | DEF | 17.50M | 108.1 | — | 20.2 | 1.15 |
| Robert Navarro | MED | 7.86M | 106.8 | 3.4 | 7.8 (-57–120) | 0.99 |
| Alfonso Herrero | POR | 17.16M | 162.2 | 5.2 | 14.2 | 0.83 |
| Facundo Bernal | MED | 14.42M | 87.4 | 3.6 | 12.0 | 0.83 |
| Roberto Fernandez | DEL | 32.50M | 130.8 | — | 26.0 (-63–175) | 0.80 |
| Miguel Sierra | DEL | 5.11M | 96.3 | — | 4.0 (-49–88) | 0.79 |
| Raphinha | DEL | 139.58M | 233.5 | — | 103.8 (-36–388) | 0.74 |
| Marc Roca | MED | 13.32M | 108.9 | 1.4 | 9.9 (-53–118) | 0.74 |
| Xavi Espart | DEF | 28.71M | 134.3 | — | 18.8 (-68–161) | 0.65 |
| Luka Sucic | MED | 10.32M | 95.0 | — | 6.7 (-39–77) | 0.65 |
| Kike Salas | DEF | 19.36M | 116.8 | — | 11.1 (-64–115) | 0.57 |
| Alvaro Valles | POR | 51.01M | 206.5 | 12.8 | 27.8 (-103–198) | 0.55 |
| David Hancko | DEF | 38.54M | 136.8 | — | 20.4 (-71–172) | 0.53 |
| Nahuel Tenaglia | DEF | 34.42M | 128.5 | — | 17.8 (-60–142) | 0.52 |
| Lucas Boye | DEL | 27.95M | 112.7 | — | 12.9 (-54–125) | 0.46 |
| Ante Budimir | DEL | 62.29M | 138.9 | — | 28.7 (-62–192) | 0.46 |
| David Soria | POR | 35.80M | 163.3 | 5.3 | 15.5 (-62–111) | 0.43 |
| Javier Hernandez | MED | 16.08M | 101.9 | — | 6.9 (-53–102) | 0.43 |
| Juan Foyth | DEF | 18.98M | 115.9 | 0.0 | 7.2 (-67–123) | 0.38 |
| Pierre-Emerick Aubameyang | DEL | 97.48M | 140.4 | — | 35.8 (-57–193) | 0.37 |
| Aitor Paredes | DEF | 13.02M | 102.4 | 2.4 | 4.2 (-51–89) | 0.33 |
| Giuliano Simeone | DEL | 44.65M | 114.4 | — | 12.6 (-64–147) | 0.28 |
| Odysseas Vlachodimos | POR | 30.03M | 147.9 | — | 8.3 (-60–83) | 0.28 |
| Marc Bernal | MED | 25.49M | 103.5 | — | 7.0 (-52–109) | 0.27 |
| Mariano Diaz | DEL | 18.54M | 100.2 | — | 5.0 (-55–107) | 0.27 |
| Cesar Tarrega | DEF | 8.89M | 89.5 | — | 1.9 (-46–75) | 0.21 |
| Luismi Cruz | DEL | 9.48M | 89.5 | — | 1.9 (-45–79) | 0.20 |
| Rodrigo Riquelme | MED | 17.68M | 89.2 | 1.0 | 3.4 (-38–71) | 0.19 |
| Toni Martinez | DEL | 28.03M | 104.8 | — | 4.5 | 0.16 |
| Alvaro Garcia | MED | 29.31M | 97.0 | — | 3.9 (-52–90) | 0.13 |
| Fran Garcia | DEF | 30.33M | 103.4 | 2.4 | 3.6 (-51–87) | 0.12 |
| Enes Unal | DEL | 17.63M | 86.1 | 1.1 | 1.6 (-44–68) | 0.09 |
| Unai Nuñez | DEF | 10.33M | 85.9 | — | 0.9 (-32–47) | 0.08 |
| Jonny Castro | DEF | 14.03M | 88.6 | — | 0.6 | 0.04 |
| Sergio Herrera | POR | 33.61M | 122.0 | — | 1.1 (-23–32) | 0.03 |
| Lorenzo Amatucci | MED | 20.08M | 79.6 | — | 0.6 (-29–40) | 0.03 |
| Jon Aramburu | DEF | 19.60M | 75.1 | — | 0.5 (-22–30) | 0.03 |
| Abel Bretones | DEF | 10.04M | 80.0 | — | 0.1 (-26–40) | 0.01 |
| Jose Gaya | DEF | 19.04M | 87.4 | — | 0.2 (-37–60) | 0.01 |
| Aimar Oroz | MED | 25.62M | 75.6 | — | 0.2 (-30–48) | 0.01 |

_20 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
