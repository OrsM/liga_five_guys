# The simulation — 2026-09-18T2056Z

## Now

**Locks in 2 days** · squad 292.75M · cash 13.22M · total 305.97M

play 5-4-1 · finish 2.21 · win 35% · season 1,356–2,219

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.62 | bench | — | -4 (-62–+34) | +3 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.50 | yours | — | +0 (-57–+43) | +1 | — |
| **Your eleven — play 5-4-1** | | | **51.35** | vs BurtonGM89 **50.50** | | **+0.86** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.21 | yours | — | -0 (-43–+32) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | +0 (-14–+12) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -6 (-78–+45) | +3 | — |
| Ali Houary | DEL | 31% | 0.92 | yours | — | +0 (-10–+9) | -49 | — |
| **BUY — free agents — none clear the bar today** | | | | | | | |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.89 | Albert | 15.53M | +20 (-84–+166) sell Carreira | +50 | 2.6 |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 308 | 13.22M | 1,763 | 1,356–2,219 | — |
| BurtonGM89 | 251 | ~-44.11M | 1,709 | 1,332–2,143 | 54% |
| Albert Laporta | 214 | ~141.27M | 1,677 | 1,239–2,188 | 58% |
| SusoGattuso | 211 | ~-2.99M | 1,493 | 1,160–1,863 | 74% |
| Magic Mike 333 | 232 | ~23.28M | 1,148 | 859–1,473 | 94% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 18 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.121 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
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
| Unai Lopez | MED | 15.53M | 125.6 | — | 50.2 | 3.23 |
| Hugo Gonzalez | MED | 1.62M | 80.5 | — | 5.1 | 3.13 |
| Pape Gueye | MED | 32.51M | 131.8 | — | 56.4 | 1.73 |
| Leandro Cabrera | DEF | 17.50M | 114.6 | — | 26.0 | 1.49 |
| Robert Navarro | MED | 7.86M | 106.6 | 3.4 | 6.9 (-61–114) | 0.88 |
| Marc Roca | MED | 13.32M | 107.9 | — | 10.3 (-51–116) | 0.77 |
| Kike Salas | DEF | 19.36M | 117.9 | — | 11.2 (-54–121) | 0.58 |
| David Soria | POR | 35.82M | 162.3 | — | 15.6 (-68–125) | 0.44 |
| Lucas Boye | DEL | 27.95M | 114.8 | — | 12.1 (-57–140) | 0.43 |
| Nahuel Tenaglia | DEF | 34.20M | 128.4 | — | 13.4 (-59–144) | 0.39 |
| Toni Martinez | DEL | 28.03M | 110.0 | — | 10.4 (-59–133) | 0.37 |
| Giuliano Simeone | DEL | 44.65M | 119.2 | — | 16.4 (-61–154) | 0.37 |
| Alvaro Garcia | MED | 29.31M | 108.5 | — | 9.4 (-59–122) | 0.32 |
| Odysseas Vlachodimos | POR | 30.35M | 148.0 | — | 9.3 (-61–88) | 0.31 |
| Juan Foyth | DEF | 18.98M | 113.3 | — | 5.8 (-64–116) | 0.30 |
| Aitor Paredes | DEF | 12.97M | 102.5 | 2.4 | 3.1 (-52–87) | 0.24 |
| Marc Bernal | MED | 26.13M | 103.3 | — | 5.1 (-49–107) | 0.20 |
| Joaquin Muñoz | DEL | 4.26M | 82.4 | — | 0.7 (-37–60) | 0.16 |
| Fran Garcia | DEF | 30.43M | 101.4 | — | 2.2 (-50–83) | 0.07 |
| Aimar Oroz | MED | 25.62M | 75.2 | — | 1.6 (-28–48) | 0.06 |
| Lorenzo Amatucci | MED | 20.08M | 79.1 | — | 0.7 (-28–41) | 0.03 |
| Jose Gaya | DEF | 19.04M | 89.0 | — | 0.4 | 0.02 |
| Gorka Guruzeta | DEL | 21.30M | 70.0 | 1.2 | 0.0 (-21–27) | 0.00 |

_24 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
