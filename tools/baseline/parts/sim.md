# The simulation — 2026-09-19T0801Z

## Now

**Locks in 3 days** · squad 295.32M · cash 13.32M · total 308.64M

play 5-4-1 · finish 2.23 · win 34% · season 1,354–2,225

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.62 | bench | — | -5 (-61–+33) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.50 | yours | — | +0 (-59–+43) | +0 | — |
| **Your eleven — play 5-4-1** | | | **51.34** | vs BurtonGM89 **47.80** | | **+3.55** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.21 | yours | — | +0 (-47–+32) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -0 (-14–+11) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -5 (-78–+40) | +0 | — |
| Ali Houary | DEL | 31% | 1.24 | yours | — | +0 (-10–+9) | -51 | — |
| **OFFERS — someone wants him** | | | | | | | | |
| Pablo Fornals | MED | 75% | 4.13 | yours | offer 48.07M (+4% vs 46.14M going rate) | -59 (-224–+40) | +61 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | offer 1.76M (+4% vs 1.69M going rate) | -0 (-14–+11) | -19 | — |
| Yeray Alvarez | DEF | 65% | 2.62 | yours | offer 7.73M (+3% vs 7.47M going rate) | -5 (-61–+33) | +2 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | offer 15.59M (-5% vs 16.44M going rate) | -5 (-78–+40) | +0 | — |
| Carl Starfelt | DEF | 35% | 2.21 | yours | offer 9.19M (-7% vs 9.87M going rate) | +0 (-47–+32) | -1 | — |
| Ali Houary | DEL | 31% | 1.24 | yours | offer 0.92M (-10% vs 1.02M going rate) | +0 (-10–+9) | -51 | — |
| **BUY — free agents** | | | | | | | | |
| Pape Gueye | MED | 76% | 6.64 | free agent | 32.74M | +4 (-173–+168) sell Alonso | +56 | 1.9 |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.89 | Albert | 15.81M | +16 (-77–+171) sell Starfelt | +50 | 2.5 |
| **PASS** | | | | | | | | |
| Hugo Gonzalez | MED | 55% | 3.40 | free agent | -1.62M | +1 (-26–+38) | +5 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 308 | 13.32M | 1,760 | 1,354–2,225 | — |
| BurtonGM89 | 254 | ~-34.02M | 1,699 | 1,316–2,125 | 54% |
| Albert Laporta | 224 | ~-10.05M | 1,684 | 1,222–2,202 | 57% |
| SusoGattuso | 211 | ~-2.89M | 1,490 | 1,149–1,850 | 73% |
| Magic Mike 333 | 236 | ~164.81M | 1,151 | 861–1,485 | 93% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 18 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| Jornada 7 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000 | a clause runs a median 1.00x market value here (n=2 real raids) and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
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
| Jose Angel Lopez | DEF | 491K | 129.3 | — | 40.3 | 81.98 |
| Unai Lopez | MED | 15.81M | 125.6 | — | 50.2 | 3.18 |
| Pape Gueye | MED | 32.51M | 131.8 | — | 56.4 | 1.74 |
| Leandro Cabrera | DEF | 17.50M | 111.0 | — | 22.0 | 1.25 |
| Nahuel Tenaglia | DEF | 34.06M | 128.5 | — | 39.4 | 1.16 |
| Robert Navarro | MED | 7.86M | 106.6 | 3.4 | 7.2 (-61–122) | 0.91 |
| Marc Roca | MED | 13.32M | 107.9 | — | 9.0 (-50–113) | 0.68 |
| Kike Salas | DEF | 19.36M | 117.9 | — | 11.4 (-54–121) | 0.59 |
| Lucas Boye | DEL | 27.95M | 115.0 | — | 15.3 (-55–136) | 0.55 |
| David Soria | POR | 35.88M | 162.3 | — | 17.2 (-59–114) | 0.48 |
| Toni Martinez | DEL | 28.03M | 110.0 | — | 11.4 (-56–133) | 0.41 |
| Giuliano Simeone | DEL | 44.65M | 119.2 | — | 16.6 (-61–148) | 0.37 |
| Aitor Paredes | DEF | 12.83M | 102.5 | 2.4 | 4.6 (-50–87) | 0.36 |
| Hugo Gonzalez | MED | 1.62M | 80.5 | — | 0.6 (-26–38) | 0.35 |
| Alvaro Garcia | MED | 29.31M | 108.6 | — | 10.0 (-58–123) | 0.34 |
| Juan Foyth | DEF | 18.98M | 113.3 | — | 6.3 (-63–120) | 0.33 |
| Odysseas Vlachodimos | POR | 30.59M | 148.0 | — | 7.2 (-59–90) | 0.24 |
| Marc Bernal | MED | 26.56M | 103.3 | — | 6.1 (-49–107) | 0.23 |
| Joaquin Muñoz | DEL | 4.26M | 82.4 | — | 0.9 (-37–60) | 0.21 |
| Iñigo Arguibide | DEF | 3.46M | 74.8 | — | 0.5 (-21–29) | 0.13 |
| Jonny Castro | DEF | 14.37M | 88.6 | — | 1.6 (-28–46) | 0.11 |
| Fran Garcia | DEF | 30.35M | 101.4 | — | 1.8 (-47–84) | 0.06 |
| Lorenzo Amatucci | MED | 20.08M | 77.8 | — | 1.1 (-28–40) | 0.06 |
| Aimar Oroz | MED | 25.62M | 75.3 | — | 0.8 (-27–43) | 0.03 |
| Lucas Noubi | DEF | 18.69M | 76.2 | — | 0.5 (-17–24) | 0.03 |
| Williot Swedberg | MED | 13.88M | 75.3 | — | 0.3 (-25–35) | 0.02 |
| Jon Aramburu | DEF | 19.78M | 75.2 | — | 0.3 (-21–28) | 0.02 |
| Gorka Guruzeta | DEL | 21.30M | 70.0 | 1.2 | 0.0 (-19–28) | 0.00 |

_21 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
