# The simulation — 2026-09-17T0607Z

## Now

**Locks in 29h** · squad 287.86M · cash 7.62M · total 295.48M

play 5-4-1 · finish 2.59 · win 26% · season 730–2,650

## Every player you could hold

| Player | Pos | Start | xPts/j | Where | € | Season | PAR | pts/M€ |
|---|---|--:|--:|---|--:|--:|--:|--:|
| **PUT ON** | | | | | | | | |
| Yeray Alvarez | DEF | 65% | 2.61 | bench | — | -1 (-55–+41) | +2 | — |
| **TAKE OFF** | | | | | | | | |
| Sergio Carreira | DEF | 50% | 2.53 | yours | — | +4 (-60–+52) | +1 | — |
| **Your eleven — play 5-4-1** | | | **48.09** | vs BurtonGM89 **45.56** | | **+2.53** | |
| **KEEP — bench** | | | | | | | | |
| Carl Starfelt | DEF | 35% | 2.24 | yours | — | +1 (-46–+36) | -1 | — |
| Alvaro Mantilla | DEF | 81% | 2.12 | yours | — | +0 (-15–+13) | -19 | — |
| Omar El Hilali | DEF | 0% | 0.00 | yours | — | +2 (-83–+70) | +8 | — |
| Ali Houary | DEL | 31% | 0.89 | yours | — | +0 (-3–+4) | -41 | — |
| **BUY — free agents** | | | | | | | | |
| Facundo Bernal | MED | 63% | 2.80 | free agent | 14.22M +0.20M | +3 (-97–+100) sell Carreira | +11 | 0.4 |
| **RAID — a clause, cannot be refused** | | | | | | | | |
| Alfonso Herrero | POR | 94% | 4.86 | Magic | 17.16M | +7 (-114–+146) sell Hilali | +14 | 10.0 |
| Lorenzo Amatucci | MED | 83% | 2.90 | SusoGattuso | 18.00M +2.07M | -0 (-111–+94) sell Hilali | +4 | -0.0 |
| Leandro Cabrera | DEF | 85% | 4.68 | Albert | 11.17M +6.33M | -1 (-91–+107) sell Hilali | +20 | -0.7 |
| **SAVE — better than yours, out of reach** | | | | | | | | |
| Raphinha | DEL | 78% | 7.56 | Magic | 86.17M short | +8 (-89–+383) if you could | +133 | 0.1 |
| Ante Budimir | DEL | 84% | 5.89 | BurtonGM89 | 8.88M short | -6 (-107–+189) if you could | +39 | -0.7 |
| Pierre-Emerick Aubameyang | DEL | 88% | 5.46 | Albert | 44.07M short | -6 (-99–+179) if you could | +40 | -0.1 |
| **PASS** | | | | | | | | |
| Alvaro Valles | POR | 76% | 10.42 | free agent | -51.01M | +5 (-112–+184) | +56 | — |

_How to read this table: **How to read the tables** in METHOD.md._

## Where the league stands

| Manager | now | cash | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|--:|
| miguel_autentico **(you)** | 291 | 7.62M | 1,768 | 730–2,650 | — |
| BurtonGM89 | 241 | ~3.11M | 1,668 | 661–2,604 | 54% |
| SusoGattuso | 210 | ~-14.50M | 1,657 | 627–2,388 | 58% |
| Albert Laporta | 217 | ~49.47M | 1,453 | 545–2,314 | 64% |
| Magic Mike 333 | 233 | ~19.78M | 1,267 | 527–2,215 | 66% |

## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 14 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.114 | a clause runs above market value here — too few real raids logged yet to median and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 32 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=1.0 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1744 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.114 fitted vs 0.113 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |

## Every listed player, compared

PAR = season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours). Comparable across positions on that basis; a good player can still show a modest PAR if his position is deep league-wide. Parenthesised range is a real simulated band where one was run; a plain figure is the point estimate. Only players ABOVE replacement are listed — see the note below the table for the rest.

| Player | Pos | Price | Season | Next | PAR | pts/€M |
|---|---|---:|---:|---:|---:|---:|
| Unai Lopez | MED | 15.21M | 118.8 | — | 43.3 | 2.85 |
| Leandro Cabrera | DEF | 17.50M | 108.1 | — | 19.5 | 1.12 |
| Alfonso Herrero | POR | 17.16M | 161.8 | 4.9 | 13.9 | 0.81 |
| Facundo Bernal | MED | 14.42M | 86.6 | 2.8 | 11.2 | 0.77 |
| Fran Garcia | DEF | 30.33M | 104.7 | 3.7 | 16.1 | 0.53 |
| Lorenzo Amatucci | MED | 20.08M | 79.6 | — | 4.2 | 0.21 |
| David Soria | POR | 35.80M | 163.2 | 5.1 | 4.1 (-61–110) | 0.12 |
| Alvaro Valles | POR | 51.01M | 204.2 | 10.4 | 5.3 (-112–184) | 0.10 |
| Raphinha | DEL | 139.58M | 233.7 | — | 7.6 (-89–383) | 0.05 |
| Jose Gaya | DEF | 19.04M | 89.3 | — | 0.7 | 0.04 |
| Dani Ceballos | MED | 19.64M | 66.9 | 1.7 | 0.3 (-9–17) | 0.02 |
| Sergio Herrera | POR | 33.61M | 122.0 | — | 0.2 (-23–30) | 0.01 |

_50 more listed players at or below the league's own replacement level at their slot, not worth a look today — a free agent nobody wants yet, or a rival's clause on a squad player he outgrew._
