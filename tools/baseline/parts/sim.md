# The simulation — 2026-09-18T1850Z

## Now

**Locks in 2 days** · squad 289.20M · cash 13.22M (22.52M already bid) · total 302.42M

play 5-4-1 · finish 2.13 · win 40% · season 1,350–2,210

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.62 | bench | — | -6 (-62–+32) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.50 | yours | — | +0 (-57–+45) | +0 | — |
| **Your eleven — play 5-4-1** | | | **51.31** | vs BurtonGM89 **49.17** | | **+2.14** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.21 | yours | — | +1 (-46–+33) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -1 (-15–+11) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -7 (-85–+44) | +3 | — |
| Ali Houary | DEL | 31% | 0.91 | yours | — | +0 (-6–+5) | -49 | — |
| **BUY — free agents** | | | | | | | | |
| Angel Perez | DEL | 83% | 3.98 | free agent | 22.52M | +38 (-66–+224) sell Starfelt | +43 | 3.3 |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.89 | Albert | 15.53M | +16 (-76–+167) sell Starfelt | +59 | 2.7 |
| Nahuel Tenaglia | DEF | 79% | 3.65 | BurtonGM89 | 34.20M | -4 (-166–+151) sell Natan | +39 | -1.5 |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 78% | 7.51 | Magic | 83.08M short | +107 (-37–+376) if you could | +125 | 1.3 |
| **PASS** | | | | | | | | |
| Marcos Fernandez | DEL | 42% | 3.43 | free agent | -1.16M | -2 (-43–+71) | -21 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 308 | 13.22M | 1,762 | 1,350–2,210 | — |
| BurtonGM89 | 253 | ~7.01M | 1,650 | 1,258–2,107 | 60% |
| Albert Laporta | 217 | ~141.27M | 1,510 | 1,100–1,961 | 70% |
| SusoGattuso | 211 | ~-11.20M | 1,494 | 1,159–1,874 | 73% |
| Magic Mike 333 | 232 | ~23.28M | 1,333 | 991–1,728 | 84% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 18 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.550 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 32 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=0.2778077287667853 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1808 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.114 fitted vs 0.113 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |

## Every listed player, compared

PAR = season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours). Comparable across positions on that basis; a good player can still show a modest PAR if his position is deep league-wide. Parenthesised range is a real simulated band where one was run; a plain figure is the point estimate. Only players ABOVE replacement are listed — see the note below the table for the rest.

| Player | Pos | Price | Season | Next | PAR | pts/€M |
|---|---|---:|---:|---:|---:|---:|
| Unai Lopez | MED | 15.53M | 125.6 | — | 58.9 | 3.79 |
| Angel Perez | DEL | 21.38M | 152.1 | — | 43.1 | 2.02 |
| Leandro Cabrera | DEF | 17.50M | 114.6 | — | 25.6 | 1.46 |
| Yassir Zabiri | DEL | 13.28M | 125.9 | — | 16.8 | 1.27 |
| Nahuel Tenaglia | DEF | 34.20M | 128.4 | — | 39.4 | 1.15 |
| Jose Maria Gimenez | DEF | 9.48M | 99.3 | — | 10.3 | 1.08 |
| Robert Navarro | MED | 7.86M | 106.6 | 3.4 | 8.1 (-61–124) | 1.03 |
| Raphinha | DEL | 141.43M | 234.1 | — | 107.1 (-37–376) | 0.76 |
| Marc Roca | MED | 13.32M | 107.9 | — | 9.7 (-52–116) | 0.73 |
| Lucas Boye | DEL | 27.95M | 114.2 | — | 14.9 (-54–136) | 0.53 |
| David Soria | POR | 35.82M | 162.3 | — | 18.3 (-65–127) | 0.51 |
| Kike Salas | DEF | 19.36M | 117.9 | — | 9.2 (-57–121) | 0.48 |
| Toni Martinez | DEL | 28.03M | 109.0 | — | 10.1 (-56–130) | 0.36 |
| Alex Berenguer | DEL | 15.51M | 99.0 | 2.8 | 5.3 (-56–107) | 0.34 |
| Giuliano Simeone | DEL | 44.65M | 118.1 | — | 14.3 (-60–148) | 0.32 |
| Juan Foyth | DEF | 18.98M | 113.3 | — | 5.6 (-62–117) | 0.30 |
| Alvaro Garcia | MED | 29.31M | 108.5 | — | 8.0 (-60–123) | 0.27 |
| Aitor Paredes | DEF | 12.97M | 102.5 | 2.4 | 3.5 (-51–91) | 0.27 |
| Odysseas Vlachodimos | POR | 30.35M | 148.0 | — | 7.4 (-58–89) | 0.24 |
| Marc Bernal | MED | 26.13M | 103.3 | — | 6.2 (-49–99) | 0.24 |
| Unai Nuñez | DEF | 10.33M | 85.9 | — | 0.5 (-29–42) | 0.05 |
| Aimar Oroz | MED | 25.62M | 75.2 | — | 0.7 (-27–42) | 0.03 |
| Cesar Tarrega | DEF | 8.89M | 89.6 | — | 0.2 (-48–70) | 0.02 |
| Williot Swedberg | MED | 13.88M | 75.4 | — | 0.2 (-25–34) | 0.02 |
| Iñigo Arguibide | DEF | 3.47M | 74.8 | — | 0.0 (-22–29) | 0.01 |
| Fran Garcia | DEF | 30.43M | 101.4 | — | 0.3 (-50–82) | 0.01 |
| Lorenzo Amatucci | MED | 20.08M | 79.1 | — | 0.2 (-28–40) | 0.01 |

_23 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
