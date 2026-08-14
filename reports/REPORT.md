# Liga Five Guys — one report — 2026-08-14 10:40 UTC

Everything the generators produced, in reading order. Sections that appeared twice are printed once.

- Decide today
- Rivals — cash, premiums, squads
- Who to buy
- Squad detail

## Decide today


### Needs a decision

- **Only 1 portero** — one knock and you can't field a legal XI.
- **Only 1 delantero** — one knock and you can't field a legal XI.
- **3 of the XI are unmodelled** (Alvaro Fernandez, Dani Lorenzo, Iñigo Vicente) — no LaLiga record, so they're carrying an assumed baseline, not an earned one.

**Squad 122.60M** · cash 111.37M · total 233.97M — balance last checked 2026-08-12, but the ledger moved on 2026-08-13. Re-check it.

Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger.

### Team

**4-5-1** · index 29.7 (a ranking number, not a points forecast)

| | Player | Start% | Value | 24h | Score | Last season |
|---|---|--:|--:|--:|--:|---|
| POR | Alvaro Fernandez | 20% | 4.80M | -77K | 0.9 | **assumed** |
| DEF | Carl Starfelt | 60% | 13.78M | 205K | 3.1 | 106p/19j |
| DEF | Igor Zubeldia | 80% | 9.86M | 98K | 3.0 | 90p/25j |
| DEF | Robin Le Normand | 60% | 9.84M | 12K | 2.6 | 125p/28j |
| DEF | Omar El Hilali | 80% | 8.78M | 140K | 2.4 | 101p/36j |
| MED | Jon Moncayola | 90% | 6.68M | 103K | 3.9 | 159p/36j |
| MED | Pepelu | 70% | 7.37M | -26K | 3.0 | 135p/31j |
| MED | Ruben Garcia | 60% | 13.58M | 169K | 2.9 | 174p/35j |
| MED | Iñigo Ruiz de Galarreta | 60% | 12.18M | 227K | 2.7 | 156p/34j |
| MED | Dani Lorenzo | 90% | 9.66M | 109K | 2.6 | **assumed** |
| DEL | Iñigo Vicente | 90% | 19.09M | 214K | 2.7 | **assumed** |

**Bench** — gap is what the XI index loses by playing him instead, after re-picking the formation. €/pt is his value per point of score: the sell shortlist, worst first.

| Player | Pos | Value | Score | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Beñat Turrientes | med | 6.99M | 2.5 | -0.0 | 2.78M | 6th MED — only 5 can ever play |

_A sale lands above or below value depending on who bids. Who is short in this position, and who can still afford you, is in `reports/behaviour.md`._

### Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Iñigo Ruiz de Galarreta | 12.18M | 227K | +1.90% |
| Omar El Hilali | 8.78M | 140K | +1.62% |
| Jon Moncayola | 6.68M | 103K | +1.57% |
| Carl Starfelt | 13.78M | 205K | +1.51% |
| Ruben Garcia | 13.58M | 169K | +1.26% |
| Dani Lorenzo | 9.66M | 109K | +1.14% |
| Iñigo Vicente | 19.09M | 214K | +1.14% |
| Beñat Turrientes | 6.99M | 70K | +1.01% |
| Igor Zubeldia | 9.86M | 98K | +1.01% |
| Alvaro Fernandez | 4.80M | -77K | -1.58% |

---

_621 players tracked, 510 with a probable-XI reading. Who to buy is in `reports/watchlist.md`; how your rivals bid is in `reports/behaviour.md`._

_Score = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Recommended XIs are logged to `data/decisions/squad_log.csv` for scoring against reality later._

_Generated 2026-08-14 10:40 UTC._

## Rivals — cash, premiums, squads


5 managers, 28 ledger rows, 26 market snapshots, points baseline 2025-26.

### 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 12 | 6.89M | 17.79M | 10.90M | 111.37M | 111.37M |
| Albert Laporta | 14 | 125.28M | 30.47M | -94.81M | ~5.19M | 5.19M |
| BurtonGM89 | 13 | 127.61M | 30.03M | -97.58M | ~2.42M | 2.42M |
| Magic Mike 333 | 18 | 83.70M | 0K | -83.70M | ~16.30M | 16.30M |
| SusoGattuso | 15 | 44.65M | 0K | -44.65M | ~55.35M | 55.35M |

`~` is an estimate: the starting budget less every ledger row, not an observed balance. The starting squad was dealt free, so it costs nothing here. A `—` means the ledger overdraws the budget, so the number would be fiction — see the warnings. Any time a rival mentions a balance, put it in `inputs/cash.txt` — one observed number turns their whole estimate into arithmetic.

**Cash-constrained right now:** BurtonGM89 (2.42M). Against these, open at the minimum increment — they cannot escalate.

### 2. What they pay over value

| Manager | Buys | Median premium | Range | Round bids |
|---|--:|--:|---|--:|
| miguel_autentico | 1 | +1.5% | +1.5% to +1.5% | 0/1 |
| Albert Laporta | 4 | +2.6% | +0.0% to +5.0% | 0/4 |
| BurtonGM89 | 5 | +9.2% | +0.0% to +21.6% | 2/5 |
| Magic Mike 333 | 4 | +9.9% | +2.6% to +15.9% | 3/4 |
| SusoGattuso | 1 | -0.2% | -0.2% to -0.2% | 0/1 |

**The floor has never won.** Every priced purchase in this league landed above the market value at the time: median +4.2%, -0.2% to +21.6% (n=15). The minimum legal bid is the market value, so bidding it is bidding the number 15 deals have already beaten.

A round bid was typed by a human. That is the whole of what roundness tells you — an exact bid is *not* the app's valuation and does not mean nobody competed, because the premium column two cells left already measures how far above the floor the buyer went, and none of these went to the floor. Sealed bids are paid as bid, so even a purchase at exactly the value would only have been yours if the tie-break favoured you, and that rule is not documented anywhere we can read.

| Date | Player | Buyer | Paid | Value then | Premium | Bid |
|---|---|---|--:|--:|--:|---|
| 08-13T21:24 | leandro cabrera | Albert Laporta | 17.50M | 16.67M | +5.0% | exact |
| 08-13T21:24 | arda guler | Albert Laporta | 50.84M | 50.84M | +0.0% | exact |
| 08-13T21:24 | giuliano simeone | SusoGattuso | 44.65M | 44.74M | -0.2% | exact |
| 08-13T21:24 | asier villalibre | Albert Laporta | 6.95M | 6.95M | +0.0% | exact |
| 08-13T21:24 | denis suarez | BurtonGM89 | 2.58M | 2.58M | +0.0% | exact |
| 08-12T21:24 | abde ezzalzouli | Albert Laporta | 49.99M | 48.70M | +2.6% | exact |
| 08-12T21:24 | santi comesaña | BurtonGM89 | 35.28M | 32.31M | +9.2% | exact |
| 08-12T21:24 | jon karrikaburu | Magic Mike 333 | 700K | 604K | +15.9% | round |
| 08-12T21:24 | pedro diaz | Magic Mike 333 | 2.05M | 1.86M | +9.9% | round |
| 08-12T21:24 | raphinha | Magic Mike 333 | 80.00M | 76.79M | +4.2% | round |
| 08-12T21:24 | marko dmitrovic | BurtonGM89 | 40.01M | 36.84M | +8.6% | round |
| 08-11T21:24 | giacomo quagliata | BurtonGM89 | 4.00M | 3.29M | +21.6% | round |
| 08-11T21:24 | carlos romero | BurtonGM89 | 45.74M | 40.59M | +12.7% | exact |
| 08-11T21:24 | fabio cardoso | Magic Mike 333 | 949K | 925K | +2.6% | exact |
| 08-11T21:24 | beñat turrientes | miguel_autentico | 6.89M | 6.79M | +1.5% | exact |

`~` priced against a snapshot more than 36h away and left out of the medians.

### 3. What happened next

_No horizon has elapsed inside the snapshot history yet. Needs 3 days of daily ingest past a transaction._

### 4. Squad diagnostics

| Manager | XI score | Shape | Trapped | Injured | Thin at | Unmatched |
|---|--:|---|--:|--:|---|--:|
| **miguel_autentico** | 29.7 | 4-5-1 | 4.80M | 0 | por,del | 0 |
| Albert Laporta | 33.6 | 4-3-3 | 56.84M | 0 | por | 0 |
| BurtonGM89 | 31.4 | 5-4-1 | 37.43M | 0 | por,del | 0 |
| Magic Mike 333 | 32.4 | 3-4-3 | 28.69M | 0 | por | 0 |
| SusoGattuso | 29.4 | 4-4-2 | 26.40M | 0 | por | 0 |

- miguel_autentico is carrying more mediocampista than can ever start.
- Albert Laporta is carrying more delantero than can ever start.
- BurtonGM89 is carrying more defensa than can ever start.
- Magic Mike 333 is carrying more defensa/mediocampista/delantero than can ever start.
- SusoGattuso is carrying more delantero than can ever start.

Trapped is value held in players below 50% start probability — money that cannot score. Unmatched is names in their squad missing from data/tidy, which are absent from the XI score, so a large number there means the comparison flatters you.

### 5. Who wants what

**Expect competition for these** — the position is one a rival is short in, so assume a bidding war and price accordingly.

| Player | Pos | Value | Start% | Short here |
|---|---|--:|--:|---|
| joan garcia | POR | 67.47M | 80% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| kylian mbappe | DEL | 130.36M | 70% | BurtonGM89 |
| vinicius junior | DEL | 107.58M | 90% | BurtonGM89 |
| alvaro valles | POR | 32.48M | 95% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| david soria | POR | 18.90M | 95% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| jan oblak | POR | 52.96M | 90% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| lamine yamal | DEL | 127.31M | 60% | BurtonGM89 |
| augusto batalla | POR | 40.65M | 95% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| ionut radu | POR | 39.71M | 90% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| mathew ryan | POR | 13.55M | 90% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| stole dimitrievski | POR | 13.76M | 90% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| unai simon | POR | 55.11M | 90% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

**Nobody else needs these.** Same quality, no auction — take the equivalent player here instead of paying a premium above.

| Player | Pos | Value | Start% |
|---|---|--:|--:|
| federico valverde | MED | 69.09M | 90% |
| zaid romero | DEF | 28.08M | 90% |
| florian lejeune | DEF | 38.21M | 90% |
| pablo fornals | MED | 58.30M | 80% |
| marcos alonso | DEF | 29.45M | 90% |
| german valera | MED | 23.29M | 90% |
| andrei ratiu | DEF | 35.41M | 90% |
| fermin lopez | MED | 62.91M | 70% |

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | Start% | Short |
|---|---|--:|--:|---|
| alvaro fernandez | POR | 4.80M | 20% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

---

Sections 2 and 3 are hypotheses until the sample grows: with 28 ledger rows across 5 managers, a median is one or two deals. Section 1 and section 5 are usable today.

## Who to buy


Everyone not owned by the 5 of us, 60% start or better.

Filtered to what your 111.37M of cash can reach.

### portero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| david soria | Getafe | por | 18.90M | 871K | 95% |
| alvaro valles | Betis | por | 32.48M | 680K | 95% |
| antonio sivera | Alavés | por | 31.39M | 299K | 95% |
| augusto batalla | Rayo | por | 40.65M | -363K | 95% |
| odysseas vlachodimos | Sevilla | por | 17.76M | 558K | 90% |
| stole dimitrievski | Valencia | por | 13.76M | 485K | 90% |
| mathew ryan | Levante | por | 13.55M | 0K | 90% |
| ionut radu | Celta | por | 39.71M | -350K | 90% |

### defensa

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| dakonam djene | Getafe | def | 8.24M | 375K | 90% |
| florian lejeune | Rayo | def | 38.21M | 40K | 90% |
| nahuel tenaglia | Alavés | def | 14.37M | 28K | 90% |
| adrian de la fuente | Levante | def | 14.05M | 0K | 90% |
| jorge salinas | Racing | def | 6.02M | 0K | 90% |
| jon martin | Real Sociedad | def | 30.11M | 0K | 90% |
| andrei ratiu | Rayo | def | 35.41M | -42K | 90% |
| marcos alonso | Celta | def | 29.45M | -89K | 90% |

### mediocampista

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| jon ander olasagasti | Levante | med | 4.25M | 91K | 90% |
| javi guerra | Valencia | med | 23.92M | 58K | 90% |
| german valera | Elche | med | 23.29M | -39K | 90% |
| guido rodriguez | Valencia | med | 25.91M | -150K | 90% |
| federico valverde | Real Madrid | med | 69.09M | -353K | 90% |
| david larrubia | Málaga | med | 55.46M | -602K | 90% |
| sergio canales | Racing | med | 22.65M | 903K | 80% |
| alberto moleiro | Villarreal | med | 66.76M | 870K | 80% |

### delantero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| vinicius junior | Real Madrid | del | 107.58M | 1.03M | 90% |
| ante budimir | Osasuna | del | 49.85M | 987K | 90% |
| jorge de frutos | Rayo | del | 46.97M | -727K | 90% |
| martin satriano | Getafe | del | 31.46M | -1.10M | 90% |
| georges mikautadze | Villarreal | del | 62.14M | 989K | 80% |
| isi palazon | Rayo | del | 18.16M | 752K | 80% |
| ivan romero | Levante | del | 7.17M | 112K | 80% |
| angel perez | Alavés | del | 6.74M | -69K | 80% |

---

Not all of these are purchasable today — the app deals a limited slate. Paste today's slate into the `seen` input and this list becomes the slate itself.

## Squad detail


| Manager | Players | Squad value | Spent | Raised | Cash |
|---|--:|--:|--:|--:|--:|
| **miguel_autentico** | 12 | 122.60M | 6.89M | 17.79M | 111.37M |
| Albert Laporta | 14 | 224.32M | 125.28M | 30.47M | ~5.19M |
| BurtonGM89 | 13 | 221.38M | 127.61M | 30.03M | ~2.42M |
| Magic Mike 333 | 18 | 216.54M | 83.70M | 0K | ~16.30M |
| SusoGattuso | 15 | 158.94M | 44.65M | 0K | ~55.35M |

`~` is an estimate, not an observed balance — see the basis notes at the bottom. Cash is a ceiling on what anyone can bid tomorrow, which is the point of tracking it.

### You (miguel_autentico)
12 players · 122.60M total · 7 at 70%+ · cash 111.37M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alvaro fernandez | Deportivo | por | 4.80M | -77K | 20% |
| carl starfelt | Celta | def | 13.78M | 205K | 60% |
| igor zubeldia | Real Sociedad | def | 9.86M | 98K | 80% |
| robin le normand | Atlético | def | 9.84M | 12K | 60% |
| omar el hilali | Espanyol | def | 8.78M | 140K | 80% |
| ruben garcia | Osasuna | med | 13.58M | 169K | 60% |
| iñigo ruiz de galarreta | Athletic | med | 12.18M | 227K | 60% |
| dani lorenzo | Málaga | med | 9.66M | 109K | 90% |
| pepelu | Valencia | med | 7.37M | -26K | 70% |
| beñat turrientes | Real Sociedad | med | 6.99M | 70K | 70% |
| jon moncayola | Osasuna | med | 6.68M | 103K | 90% |
| iñigo vicente | Racing | del | 19.09M | 214K | 90% |

### Albert Laporta
14 players · 224.32M total · 4 at 70%+ · cash ~5.19M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| matias dituro | Elche | por | 7.30M | 139K | 90% |
| leandro cabrera | Espanyol | def | 16.80M | 185K | 95% |
| diego javier llorente | Betis | def | 13.04M | 102K | 60% |
| juan foyth | Villarreal | def | 11.73M | 268K | 50% |
| pedro bigas | Elche | def | 5.30M | -128K | 50% |
| arda guler | Real Madrid | med | 51.28M | 606K | 60% |
| eduardo camavinga | Real Madrid | med | 10.48M | -168K | 30% |
| ilaix moriba | Celta | med | 10.25M | -104K | 50% |
| marc roca | Betis | med | 5.33M | 65K | 60% |
| abde ezzalzouli | Betis | del | 46.36M | -1.11M | 30% |
| ayoze perez | Villarreal | del | 16.82M | 397K | 50% |
| raul moro | Osasuna | del | 13.21M | 156K | 60% |
| ferran jutgla | Celta | del | 9.37M | 99K | 80% |
| asier villalibre | Racing | del | 7.05M | 113K | 70% |

### BurtonGM89
13 players · 221.38M total · 6 at 70%+ · cash ~2.42M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| marko dmitrovic | Espanyol | por | 37.42M | 327K | 95% |
| carlos romero | Villarreal | def | 43.23M | 937K | 80% |
| eder militao | Real Madrid | def | 12.40M | -383K | 0% |
| quilindschy hartman | Espanyol | def | 9.05M | -155K | 50% |
| justin de haas | Valencia | def | 8.99M | 126K | 70% |
| carlos puga | Málaga | def | 5.32M | -22K | 70% |
| giacomo quagliata | Deportivo | def | 3.29M | -6K | 50% |
| santi comesaña | Villarreal | med | 33.67M | 680K | 90% |
| tajon buchanan | Villarreal | med | 18.49M | -180K | 30% |
| antonio blanco | Alavés | med | 14.95M | -7K | 90% |
| jon gorrotxategi | Real Sociedad | med | 6.54M | -180K | 30% |
| denis suarez | Alavés | med | 2.54M | -31K | 50% |
| karl etta eyong | Levante | del | 25.48M | 288K | 50% |

### Magic Mike 333
18 players · 216.54M total · 6 at 70%+ · cash ~16.30M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alfonso herrero | Málaga | por | 8.10M | 150K | 80% |
| lucas noubi | Deportivo | def | 12.05M | 257K | 80% |
| kike salas | Sevilla | def | 11.94M | 181K | 90% |
| jose gaya | Valencia | def | 11.05M | -95K | 60% |
| raul asencio | Real Madrid | def | 4.71M | -149K | 0% |
| fabio cardoso | Sevilla | def | 860K | -19K | 0% |
| alex pastor | Málaga | def | 379K | -3K | 40% |
| gustavo puerta | Racing | med | 12.55M | 230K | 80% |
| brahim diaz | Real Madrid | med | 10.52M | -54K | 50% |
| gabriel moscardo | Espanyol | med | 9.65M | -233K | 30% |
| williot swedberg | Celta | med | 8.07M | -32K | 50% |
| marc bernal | Barcelona | med | 6.66M | 304K | 60% |
| pedro diaz | Rayo | med | 1.80M | -25K | 50% |
| raphinha | Barcelona | del | 81.98M | 2.91M | 70% |
| gorka guruzeta | Athletic | del | 13.53M | 176K | 80% |
| lucas boye | Alavés | del | 12.51M | -298K | 40% |
| pere milla | Espanyol | del | 9.59M | -60K | 50% |
| jon karrikaburu | Real Sociedad | del | 581K | -11K | 0% |

### SusoGattuso
15 players · 158.94M total · 10 at 70%+ · cash ~55.35M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| wojciech szczesny | Barcelona | por | 2.40M | -48K | 20% |
| yuri berchiche | Athletic | def | 12.55M | -40K | 70% |
| cesar tarrega | Valencia | def | 8.94M | 70K | 80% |
| abdel abqar | Getafe | def | 6.92M | 170K | 70% |
| jonny castro | Alavés | def | 5.46M | 2K | 70% |
| alvaro garcia | Villarreal | def | 502K | 0K | 80% |
| aleksa puric | Atlético | def | 435K | 0K | — |
| aimar oroz | Osasuna | med | 15.76M | 153K | 70% |
| lorenzo amatucci | Deportivo | med | 12.55M | 246K | 80% |
| izan merino | Málaga | med | 6.44M | -45K | 70% |
| johnny cardoso | Atlético | med | 5.92M | -111K | 30% |
| giuliano simeone | Atlético | del | 44.16M | -493K | 70% |
| andres martin | Racing | del | 19.25M | -92K | 80% |
| alex berenguer | Athletic | del | 9.03M | -52K | 30% |
| carlos espi | Real Madrid | del | 8.60M | 245K | 30% |

### What they pay

| Date | Player | From → To | Price |
|---|---|---|--:|
| 2026-08-11T21:24 | beñat turrientes | market → miguel_autentico | 6892898 |
| 2026-08-11T21:42 | unai egiluz | BurtonGM89 → market | 468693 |
| 2026-08-11T22:24 | dani vivian | Albert Laporta → market | 11664367 |
| 2026-08-11T22:25 | manuel fernandez | Albert Laporta → market | 373500 |
| 2026-08-11T22:25 | pathe ciss | Albert Laporta → market | 10393147 |
| 2026-08-11T22:26 | javi puado | Albert Laporta → market | 8041411 |
| 2026-08-12T21:24 | abde ezzalzouli | market → Albert Laporta | 49991863 |
| 2026-08-12T21:24 | santi comesaña | market → BurtonGM89 | 35276000 |
| 2026-08-12T21:24 | jon karrikaburu | market → Magic Mike 333 | 700000 |
| 2026-08-12T21:24 | pedro diaz | market → Magic Mike 333 | 2050000 |
| 2026-08-12T21:24 | raphinha | market → Magic Mike 333 | 80000000 |
| 2026-08-12T21:24 | marko dmitrovic | market → BurtonGM89 | 40010000 |
| 2026-08-13T12:12 | orri steinn oskarsson | miguel_autentico → market | 8567036 |
| 2026-08-13T12:12 | dani martinez | miguel_autentico → market | 425612 |
| 2026-08-13T12:13 | hugo duro | miguel_autentico → market | 8800811 |
| 2026-08-13T21:24 | leandro cabrera | market → Albert Laporta | 17500002 |
| 2026-08-13T21:24 | arda guler | market → Albert Laporta | 50836360 |
| 2026-08-13T21:24 | giuliano simeone | market → SusoGattuso | 44652302 |
| 2026-08-13T21:24 | asier villalibre | market → Albert Laporta | 6954257 |
| 2026-08-13T21:24 | denis suarez | market → BurtonGM89 | 2580315 |
| 2026-08-13T21:25 | juan musso | BurtonGM89 → market | 4748599 |
| 2026-08-13T21:26 | iago aspas | BurtonGM89 → market | 7275530 |
| 2026-08-13T21:26 | unai lopez | BurtonGM89 → market | 5762182 |
| 2026-08-13T21:26 | joaquin muñoz | BurtonGM89 → market | 6489738 |
| 2026-08-13T21:26 | aliou dieng | BurtonGM89 → market | 5284122 |

### Cash basis

- **miguel_autentico** — balance you recorded on 2026-08-12, then 3 ledger row(s) (known)
- **Albert Laporta** — 100M starting budget, then 8 ledger row(s) (estimated)
- **BurtonGM89** — 100M starting budget, then 11 ledger row(s) (estimated)
- **Magic Mike 333** — 100M starting budget, then 4 ledger row(s) (estimated)
- **SusoGattuso** — 100M starting budget, then 1 ledger row(s) (estimated)
