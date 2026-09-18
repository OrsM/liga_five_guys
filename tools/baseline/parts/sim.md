# The simulation — 2026-09-17T2134Z

## Now

**Locks in 29h** · squad 287.64M · cash 7.62M · total 295.27M

play 5-4-1 · finish 2.06 · win 43% · season 1,366–2,159

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.61 | bench | — | -6 (-61–+33) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.53 | yours | — | -2 (-62–+41) | +1 | — |
| **Your eleven — play 5-4-1** | | | **49.71** | vs Albert Laporta **44.41** | | **+5.30** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.24 | yours | — | -0 (-50–+34) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | -0 (-16–+13) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | -10 (-91–+43) | +8 | — |
| Ali Houary | DEL | 31% | 0.89 | yours | — | +0 (-6–+6) | -46 | — |
| **BUY — free agents** | | | | | | | | |
| Angel Perez | DEL | 83% | 3.99 | free agent | 21.38M | +33 (-85–+211) sell Hilali | +48 | 6.6 |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Unai Lopez | MED | 77% | 3.62 | Albert | 15.21M | +13 (-75–+148) sell Starfelt | +52 | 2.4 |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 78% | 7.57 | Magic | 86.17M short | +107 (-33–+379) if you could | +129 | 1.2 |
| Ante Budimir | DEL | 84% | 5.89 | BurtonGM89 | 8.88M short | +31 (-61–+195) if you could | +34 | 3.5 |
| **PASS** | | | | | | | | |
| Jose Maria Gimenez | DEF | 56% | 3.18 | free agent | -9.48M | +1 (-50–+85) | +11 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 308 | 7.62M | 1,754 | 1,366–2,159 | — |
| BurtonGM89 | 253 | ~3.11M | 1,638 | 1,265–2,061 | 61% |
| SusoGattuso | 208 | ~-14.50M | 1,483 | 1,153–1,845 | 74% |
| Albert Laporta | 217 | ~138.57M | 1,454 | 1,096–1,871 | 75% |
| Magic Mike 333 | 231 | ~19.78M | 1,345 | 995–1,741 | 84% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| **Albert Laporta's squad is short a position** (1 defensa) | his real squad cannot field a legal eleven, so the SIMULATION stands in a league-average player at that spot — the same real per-jornada data every other player's number comes from, not an invented figure or a presumption he never fixes it (assuming he never would is the much stronger, much less plausible claim). His true squad may be stronger or weaker than an average man there once he actually buys one |
| Jornada 6 is half played — 14 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.592 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
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
| Unai Lopez | MED | 15.21M | 118.8 | — | 51.9 | 3.41 |
| Angel Perez | DEL | 21.38M | 152.3 | — | 47.5 | 2.22 |
| Yassir Zabiri | DEL | 13.28M | 126.0 | — | 21.3 | 1.60 |
| Robert Navarro | MED | 7.86M | 106.8 | 3.4 | 9.9 (-58–124) | 1.25 |
| Ramon Terrats | MED | 8.56M | 104.5 | 0.0 | 10.2 (-45–98) | 1.19 |
| Leandro Cabrera | DEF | 17.50M | 108.1 | — | 19.5 | 1.12 |
| Miguel Sierra | DEL | 5.11M | 95.6 | — | 5.4 (-51–88) | 1.05 |
| Marc Roca | MED | 13.32M | 108.9 | 1.4 | 11.4 (-51–119) | 0.86 |
| Roberto Fernandez | DEL | 32.50M | 131.0 | — | 27.4 (-61–178) | 0.84 |
| Alfonso Herrero | POR | 17.16M | 162.2 | 5.2 | 14.2 | 0.83 |
| Raphinha | DEL | 139.58M | 233.9 | — | 107.4 (-33–379) | 0.77 |
| Williot Swedberg | MED | 13.88M | 75.5 | — | 8.7 | 0.63 |
| Xavi Espart | DEF | 28.71M | 134.3 | — | 17.8 (-65–155) | 0.62 |
| Luka Sucic | MED | 10.32M | 94.6 | — | 6.1 (-38–78) | 0.59 |
| Lucas Boye | DEL | 27.95M | 112.4 | — | 15.7 (-53–141) | 0.56 |
| Juan Foyth | DEF | 18.98M | 115.9 | 0.0 | 10.5 (-63–119) | 0.55 |
| David Hancko | DEF | 38.54M | 136.8 | — | 21.0 (-73–169) | 0.54 |
| Kike Salas | DEF | 19.36M | 116.8 | — | 10.2 (-60–128) | 0.52 |
| Ante Budimir | DEL | 62.29M | 138.9 | — | 31.4 (-61–195) | 0.50 |
| Nahuel Tenaglia | DEF | 34.42M | 128.5 | — | 17.3 (-61–139) | 0.50 |
| Javier Hernandez | MED | 16.08M | 102.0 | — | 7.1 (-53–103) | 0.44 |
| David Soria | POR | 35.80M | 163.3 | 5.3 | 14.9 (-60–109) | 0.42 |
| Aitor Paredes | DEF | 13.02M | 102.4 | 2.4 | 5.3 (-49–84) | 0.41 |
| Luismi Cruz | DEL | 9.48M | 89.5 | — | 3.5 (-45–82) | 0.37 |
| Toni Martinez | DEL | 28.03M | 104.8 | — | 9.5 (-58–124) | 0.34 |
| Mariano Diaz | DEL | 18.54M | 100.2 | — | 5.8 (-53–112) | 0.31 |
| Marc Bernal | MED | 25.49M | 103.5 | — | 7.5 (-48–102) | 0.29 |
| Giuliano Simeone | DEL | 44.65M | 114.4 | — | 12.6 (-61–141) | 0.28 |
| Odysseas Vlachodimos | POR | 30.03M | 147.9 | — | 6.7 (-59–85) | 0.22 |
| Enes Unal | DEL | 17.63M | 86.1 | 1.1 | 3.1 (-38–65) | 0.17 |
| Rodrigo Riquelme | MED | 17.68M | 89.2 | 1.0 | 3.0 (-37–72) | 0.17 |
| Jose Maria Gimenez | DEF | 9.48M | 99.2 | — | 1.5 (-50–85) | 0.16 |
| Alvaro Garcia | MED | 29.31M | 97.2 | — | 4.3 (-51–89) | 0.15 |
| Unai Nuñez | DEF | 10.33M | 85.9 | — | 1.1 (-33–45) | 0.11 |
| Fran Garcia | DEF | 30.34M | 103.4 | 2.4 | 3.2 (-49–86) | 0.11 |
| Abel Bretones | DEF | 10.04M | 80.0 | — | 1.0 (-27–36) | 0.10 |
| Cesar Tarrega | DEF | 8.89M | 89.5 | — | 0.7 (-45–68) | 0.08 |
| Lucas Noubi | DEF | 18.69M | 76.1 | — | 0.8 (-16–23) | 0.04 |
| Jon Aramburu | DEF | 19.60M | 75.1 | — | 0.5 (-22–26) | 0.02 |
| Lorenzo Amatucci | MED | 20.08M | 79.6 | — | 0.4 (-29–43) | 0.02 |
| Jose Gaya | DEF | 19.04M | 87.4 | — | 0.2 (-39–55) | 0.01 |
| Aimar Oroz | MED | 25.62M | 75.3 | — | 0.3 (-27–42) | 0.01 |
| Adria Altimira | DEF | 4.39M | 76.6 | — | 0.0 (-18–25) | 0.00 |

_18 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
