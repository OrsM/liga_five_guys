# The simulation — 2026-09-17T0801Z

## Now

**Locks in 29h** · squad 287.86M · cash 7.62M · total 295.48M

play 5-4-1 · finish 1.96 · win 46% · season 1,404–2,108

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.61 | bench | — | -8 (-59–+31) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.53 | yours | — | -2 (-58–+38) | +1 | — |
| **Your eleven — play 5-4-1** | | | **48.09** | vs BurtonGM89 **45.56** | | **+2.53** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.24 | yours | — | -2 (-50–+32) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -0 (-16–+13) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -12 (-83–+41) | +8 | — |
| Ali Houary | DEL | 31% | 0.89 | yours | — | +0 (-6–+6) | -41 | — |
| **BUY — free agents — none clear the bar today** | | | | | | | |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.62 | Albert | 15.21M | +12 (-86–+131) sell Hilali | +43 | — |
| Alvaro Garcia | MED | 75% | 2.97 | SusoGattuso | 18.02M +11.29M | -17 (-145–+99) sell Natan | +22 | — |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 78% | 7.56 | Magic | 86.17M short | +119 (-24–+359) if you could | +133 | 1.4 |
| Ante Budimir | DEL | 84% | 5.89 | BurtonGM89 | 8.88M short | +37 (-54–+178) if you could | +39 | 4.1 |
| Pierre-Emerick Aubameyang | DEL | 88% | 5.46 | Albert | 44.07M short | +42 (-52–+178) if you could | +40 | 0.9 |
| **PASS** | | | | | | | | |
| Alvaro Valles | POR | 76% | 10.42 | free agent | -51.01M | +30 (-95–+181) | +56 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 291 | 7.62M | 1,741 | 1,404–2,108 | — |
| BurtonGM89 | 241 | ~3.11M | 1,630 | 1,310–1,964 | 61% |
| SusoGattuso | 210 | ~-14.50M | 1,487 | 1,211–1,787 | 76% |
| Albert Laporta | 217 | ~49.47M | 1,414 | 1,110–1,739 | 82% |
| Magic Mike 333 | 233 | ~19.78M | 1,355 | 1,052–1,683 | 86% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 14 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.802 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 32 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=0.17655930545407905 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1744 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.114 fitted vs 0.113 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |

## Every listed player, compared

PAR = season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours). Comparable across positions on that basis; a good player can still show a modest PAR if his position is deep league-wide. Parenthesised range is a real simulated band where one was run; a plain figure is the point estimate. Only players ABOVE replacement are listed — see the note below the table for the rest.

| Player | Pos | Price | Season | Next | PAR | pts/€M |
|---|---|---:|---:|---:|---:|---:|
| Unai Lopez | MED | 15.21M | 118.8 | — | 43.3 | 2.85 |
| Ramon Terrats | MED | 8.56M | 107.4 | 2.9 | 16.9 (-43–100) | 1.98 |
| Robert Navarro | MED | 7.86M | 106.8 | 3.4 | 12.7 (-52–112) | 1.61 |
| Miguel Sierra | DEL | 5.11M | 96.0 | — | 6.2 (-45–83) | 1.22 |
| Marc Roca | MED | 13.32M | 110.1 | 2.7 | 15.4 (-47–111) | 1.16 |
| Leandro Cabrera | DEF | 17.50M | 108.1 | — | 19.5 | 1.12 |
| Roberto Fernandez | DEL | 32.50M | 130.9 | — | 33.2 (-57–164) | 1.02 |
| Luka Sucic | MED | 10.32M | 94.8 | — | 9.1 (-35–72) | 0.88 |
| Xavi Espart | DEF | 28.71M | 134.3 | — | 25.3 (-61–149) | 0.88 |
| Raphinha | DEL | 139.58M | 233.7 | — | 118.6 (-24–359) | 0.85 |
| Alfonso Herrero | POR | 17.16M | 161.8 | 4.9 | 13.9 | 0.81 |
| Kike Salas | DEF | 19.36M | 118.3 | — | 15.0 (-54–110) | 0.78 |
| Facundo Bernal | MED | 14.42M | 86.6 | 2.8 | 11.2 | 0.77 |
| Alvaro Garcia | MED | 29.31M | 97.0 | — | 21.6 | 0.74 |
| David Hancko | DEF | 38.54M | 136.8 | — | 27.0 (-61–167) | 0.70 |
| Nahuel Tenaglia | DEF | 34.42M | 128.5 | — | 22.6 (-54–133) | 0.66 |
| Lucas Boye | DEL | 27.95M | 112.6 | — | 18.0 (-47–118) | 0.64 |
| Alvaro Valles | POR | 51.01M | 204.2 | 10.4 | 30.2 (-95–181) | 0.59 |
| Ante Budimir | DEL | 62.29M | 138.9 | — | 36.8 (-54–178) | 0.59 |
| Javier Hernandez | MED | 16.08M | 101.6 | — | 9.2 (-48–93) | 0.57 |
| Juan Foyth | DEF | 18.98M | 115.9 | 0.0 | 10.1 (-60–114) | 0.53 |
| Luismi Cruz | DEL | 9.48M | 89.5 | — | 4.9 (-42–74) | 0.52 |
| Aitor Paredes | DEF | 13.02M | 102.4 | 2.4 | 6.4 (-47–84) | 0.49 |
| Mariano Diaz | DEL | 18.54M | 100.2 | — | 8.9 (-50–98) | 0.48 |
| David Soria | POR | 35.80M | 163.2 | 5.1 | 16.4 (-59–104) | 0.46 |
| Toni Martinez | DEL | 28.03M | 104.8 | — | 12.7 (-52–111) | 0.45 |
| Marc Bernal | MED | 25.49M | 103.5 | — | 11.0 (-48–101) | 0.43 |
| Cesar Tarrega | DEF | 8.89M | 89.5 | — | 3.8 (-41–68) | 0.43 |
| Pierre-Emerick Aubameyang | DEL | 97.48M | 140.4 | — | 41.6 (-52–178) | 0.43 |
| Giuliano Simeone | DEL | 44.65M | 114.4 | — | 18.1 (-58–135) | 0.41 |
| Enes Unal | DEL | 17.63M | 88.3 | 2.4 | 7.0 (-38–67) | 0.40 |
| Rodrigo Riquelme | MED | 17.68M | 90.6 | 2.4 | 6.7 (-33–69) | 0.38 |
| Fran Garcia | DEF | 30.33M | 104.7 | 3.7 | 7.7 (-45–81) | 0.25 |
| Lorenzo Amatucci | MED | 20.08M | 79.6 | — | 4.2 | 0.21 |
| Unai Nuñez | DEF | 10.33M | 85.9 | — | 1.7 (-30–44) | 0.16 |
| Jonny Castro | DEF | 14.03M | 88.6 | — | 1.6 (-30–45) | 0.12 |
| Dani Ceballos | MED | 19.64M | 66.9 | 1.7 | 1.5 (-13–22) | 0.08 |
| Abel Bretones | DEF | 10.04M | 80.0 | — | 0.6 (-24–38) | 0.06 |
| Aimar Oroz | MED | 25.62M | 75.5 | — | 1.2 (-26–42) | 0.05 |
| Sergio Herrera | POR | 33.61M | 122.0 | — | 1.3 (-22–31) | 0.04 |
| Jose Gaya | DEF | 19.04M | 89.3 | — | 0.7 | 0.04 |
| Williot Swedberg | MED | 13.88M | 75.4 | — | 0.3 (-18–26) | 0.02 |
| Gorka Guruzeta | DEL | 21.30M | 67.7 | 1.2 | 0.4 (-18–26) | 0.02 |
| Mikel Jauregizar | MED | 14.37M | 66.9 | 1.4 | 0.0 (-14–21) | 0.00 |

_18 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
