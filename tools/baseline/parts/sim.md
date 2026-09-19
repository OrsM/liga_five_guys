# The simulation — 2026-09-19T0430Z

## Now

**Locks in 3 days** · squad 295.32M · cash 13.22M · total 308.54M

play 5-4-1 · finish 2.19 · win 36% · season 1,358–2,229

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.62 | bench | — | -5 (-61–+34) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.50 | yours | — | -1 (-55–+43) | +0 | — |
| **Your eleven — play 5-4-1** | | | **51.34** | vs BurtonGM89 **47.80** | | **+3.55** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.21 | yours | — | +1 (-47–+32) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -1 (-14–+12) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -6 (-83–+41) | +0 | — |
| Ali Houary | DEL | 31% | 1.24 | yours | — | +0 (-10–+10) | -51 | — |
| **OFFERS — someone wants him** | | | | | | | | |
| Pablo Fornals | MED | 75% | 4.13 | yours | offer 48.07M (+8% vs 44.54M) · paid 58.22M, -10.15M | -57 (-227–+41) | +61 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | offer 1.76M (+7% vs 1.63M) · paid 2.30M, -0.55M | -1 (-14–+12) | -19 | — |
| Yeray Alvarez | DEF | 65% | 2.62 | yours | offer 7.73M (+7% vs 7.22M) · paid 6.93M, +0.80M | -5 (-61–+34) | +2 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | offer 15.59M (-2% vs 15.87M) | -6 (-83–+41) | +0 | — |
| Carl Starfelt | DEF | 35% | 2.21 | yours | offer 9.19M (-4% vs 9.53M) | +1 (-47–+32) | -1 | — |
| Ali Houary | DEL | 31% | 1.24 | yours | offer 0.92M (-7% vs 0.99M) · paid 1.78M, -0.86M | +0 (-10–+10) | -51 | — |
| **BUY — free agents** | | | | | | | | |
| Pape Gueye | MED | 76% | 6.64 | free agent | 32.74M | +4 (-160–+173) sell Natan | +57 | — |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.89 | Albert | 15.81M | +17 (-77–+168) sell Starfelt | +50 | 2.8 |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 308 | 13.22M | 1,765 | 1,358–2,229 | — |
| BurtonGM89 | 251 | ~-34.12M | 1,699 | 1,317–2,132 | 55% |
| Albert Laporta | 214 | ~-10.15M | 1,666 | 1,226–2,151 | 59% |
| SusoGattuso | 211 | ~-2.99M | 1,487 | 1,144–1,869 | 74% |
| Magic Mike 333 | 232 | ~164.71M | 1,148 | 850–1,486 | 94% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 18 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| Jornada 7 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.017 | a clause runs a median 1.00x market value here (n=2 real raids) and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
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
| Unai Lopez | MED | 15.53M | 125.6 | — | 50.3 | 3.24 |
| Pape Gueye | MED | 32.51M | 131.8 | — | 56.5 | 1.74 |
| Leandro Cabrera | DEF | 17.50M | 111.0 | — | 22.0 | 1.25 |
| Robert Navarro | MED | 7.86M | 106.6 | 3.4 | 7.4 (-60–125) | 0.94 |
| Marc Roca | MED | 13.32M | 107.9 | — | 10.8 (-52–111) | 0.81 |
| Kike Salas | DEF | 19.36M | 117.9 | — | 10.7 (-59–124) | 0.55 |
| Lucas Boye | DEL | 27.95M | 115.0 | — | 13.8 (-58–140) | 0.49 |
| David Soria | POR | 35.82M | 162.3 | — | 17.4 (-58–122) | 0.48 |
| Juan Foyth | DEF | 18.98M | 113.3 | — | 8.4 (-64–118) | 0.44 |
| Toni Martinez | DEL | 28.03M | 110.0 | — | 11.8 (-55–129) | 0.42 |
| Nahuel Tenaglia | DEF | 34.20M | 128.5 | — | 14.3 (-58–144) | 0.42 |
| Giuliano Simeone | DEL | 44.65M | 119.2 | — | 14.7 (-59–152) | 0.33 |
| Alvaro Garcia | MED | 29.31M | 108.6 | — | 8.4 (-58–122) | 0.29 |
| Odysseas Vlachodimos | POR | 30.35M | 148.0 | — | 8.4 (-59–88) | 0.28 |
| Joaquin Muñoz | DEL | 4.26M | 82.4 | — | 1.0 (-37–61) | 0.24 |
| Marc Bernal | MED | 26.13M | 103.3 | — | 5.6 (-49–103) | 0.21 |
| Aitor Paredes | DEF | 12.97M | 102.5 | 2.4 | 2.2 (-51–94) | 0.17 |
| Fran Garcia | DEF | 30.43M | 101.4 | — | 3.2 (-53–82) | 0.10 |
| Aimar Oroz | MED | 25.62M | 75.3 | — | 1.3 (-27–44) | 0.05 |
| Williot Swedberg | MED | 13.88M | 75.3 | — | 0.5 (-27–35) | 0.03 |
| Lorenzo Amatucci | MED | 20.08M | 77.8 | — | 0.7 (-29–40) | 0.03 |
| Lucas Noubi | DEF | 18.69M | 76.2 | — | 0.2 (-18–24) | 0.01 |

_23 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
