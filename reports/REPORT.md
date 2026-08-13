# Liga Five Guys — one report — 2026-08-13 19:46 UTC

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

**Squad 121.35M** · cash 111.37M · total 232.73M — balance last checked 2026-08-12, but the ledger moved on 2026-08-13. Re-check it.

Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger.

### Team

**4-5-1** · index 29.7 (a ranking number, not a points forecast)

| | Player | Start% | Value | 24h | Score | Last season |
|---|---|--:|--:|--:|--:|---|
| POR | Alvaro Fernandez | 20% | 4.87M | -94K | 0.9 | **assumed** |
| DEF | Carl Starfelt | 60% | 13.57M | 58K | 3.1 | 106p/19j |
| DEF | Igor Zubeldia | 80% | 9.76M | 30K | 3.0 | 90p/25j |
| DEF | Robin Le Normand | 60% | 9.83M | -105K | 2.6 | 125p/28j |
| DEF | Omar El Hilali | 80% | 8.64M | 81K | 2.4 | 101p/36j |
| MED | Jon Moncayola | 90% | 6.58M | 56K | 3.9 | 159p/36j |
| MED | Pepelu | 70% | 7.40M | -60K | 3.0 | 135p/31j |
| MED | Ruben Garcia | 60% | 13.41M | 148K | 2.9 | 174p/35j |
| MED | Iñigo Ruiz de Galarreta | 60% | 11.95M | 57K | 2.7 | 156p/34j |
| MED | Dani Lorenzo | 90% | 9.55M | 38K | 2.6 | **assumed** |
| DEL | Iñigo Vicente | 90% | 18.88M | 22K | 2.7 | **assumed** |

**Bench** — gap is what the XI index loses by playing him instead, after re-picking the formation. €/pt is his value per point of score: the sell shortlist, worst first.

| Player | Pos | Value | Score | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Beñat Turrientes | med | 6.92M | 2.5 | -0.0 | 2.76M | 6th MED — only 5 can ever play |

_A sale lands above or below value depending on who bids. Who is short in this position, and who can still afford you, is in `reports/behaviour.md`._

### Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Ruben Garcia | 13.41M | 148K | +1.11% |
| Robin Le Normand | 9.83M | -105K | -1.06% |
| Alvaro Fernandez | 4.87M | -94K | -1.89% |

---

_621 players tracked, 510 with a probable-XI reading. Who to buy is in `reports/watchlist.md`; how your rivals bid is in `reports/behaviour.md`._

_Score = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Recommended XIs are logged to `data/decisions/squad_log.csv` for scoring against reality later._

_Generated 2026-08-13 19:46 UTC._

## Rivals — cash, premiums, squads


5 managers, 18 ledger rows, 22 market snapshots, points baseline 2025-26.

### 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 12 | 6.89M | 17.79M | 10.90M | 111.37M | 111.37M |
| Albert Laporta | 11 | 49.99M | 30.47M | -19.52M | ~80.48M | 80.48M |
| BurtonGM89 | 17 | 125.03M | 469K | -124.56M | — | — |
| Magic Mike 333 | 18 | 83.70M | 0K | -83.70M | ~16.30M | 16.30M |
| SusoGattuso | 14 | 0K | 0K | 0K | ~100.00M | 100.00M |

`~` is an estimate: the starting budget less every ledger row, not an observed balance. The starting squad was dealt free, so it costs nothing here. A `—` means the ledger overdraws the budget, so the number would be fiction — see the warnings. Any time a rival mentions a balance, put it in `inputs/cash.txt` — one observed number turns their whole estimate into arithmetic.

### 2. What they pay over value

| Manager | Buys | Median premium | Range | Round bids |
|---|--:|--:|---|--:|
| miguel_autentico | 1 | +1.5% | +1.5% to +1.5% | 0/1 |
| Albert Laporta | 1 | +2.6% | +2.6% to +2.6% | 0/1 |
| BurtonGM89 | 4 | +12.7% | +8.6% to +21.6% | 2/4 |
| Magic Mike 333 | 4 | +9.9% | +2.6% to +15.9% | 3/4 |

A round bid was chosen by a human bidding against someone. An exact one is the app's own valuation, which means nobody competed — every exact purchase in the table below was a player you could have had for the same money.

| Date | Player | Buyer | Paid | Value then | Premium | Bid |
|---|---|---|--:|--:|--:|---|
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
| **miguel_autentico** | 29.7 | 4-5-1 | 4.87M | 0 | por,del | 0 |
| Albert Laporta | **illegal** | — | 58.12M | 0 | por,def,med | 0 |
| BurtonGM89 | 33.9 | 4-3-3 | 55.08M | 0 | — | 0 |
| Magic Mike 333 | 32.4 | 3-4-3 | 29.41M | 0 | por | 0 |
| SusoGattuso | 27.1 | 4-4-2 | 26.36M | 0 | por | 0 |

- miguel_autentico is carrying more mediocampista than can ever start.
- **Albert Laporta cannot field a legal XI** — short at no legal shape. They have to buy there before the next lock, whatever it costs, which is the one situation where their premium goes out of the window.
- Albert Laporta is carrying more delantero than can ever start.
- BurtonGM89 is carrying more mediocampista/defensa/portero than can ever start.
- Magic Mike 333 is carrying more defensa/mediocampista/delantero than can ever start.

Trapped is value held in players below 50% start probability — money that cannot score. Unmatched is names in their squad missing from data/tidy, which are absent from the XI score, so a large number there means the comparison flatters you.

### 5. Who wants what

**Expect competition for these** — the position is one a rival is short in, so assume a bidding war and price accordingly.

| Player | Pos | Value | Start% | Short here |
|---|---|--:|--:|---|
| joan garcia | POR | 66.03M | 80% | Albert Laporta, Magic Mike 333, SusoGattuso |
| alvaro valles | POR | 31.80M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| federico valverde | MED | 69.44M | 90% | Albert Laporta |
| david soria | POR | 18.03M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| jan oblak | POR | 53.40M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| augusto batalla | POR | 41.01M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| ionut radu | POR | 40.06M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| zaid romero | DEF | 29.06M | 90% | Albert Laporta |
| mathew ryan | POR | 13.55M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| florian lejeune | DEF | 38.17M | 90% | Albert Laporta |
| pablo fornals | MED | 58.77M | 80% | Albert Laporta |
| stole dimitrievski | POR | 13.28M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |

**Nobody else needs these.** Same quality, no auction — take the equivalent player here instead of paying a premium above.

| Player | Pos | Value | Start% |
|---|---|--:|--:|
| kylian mbappe | DEL | 130.53M | 70% |
| vinicius junior | DEL | 106.55M | 90% |
| lamine yamal | DEL | 127.78M | 60% |
| ante budimir | DEL | 48.86M | 90% |
| martin satriano | DEL | 32.56M | 90% |
| georges mikautadze | DEL | 61.15M | 80% |
| jorge de frutos | DEL | 47.70M | 90% |
| nicolas pepe | DEL | 49.14M | 70% |

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | Start% | Short |
|---|---|--:|--:|---|
| alvaro fernandez | POR | 4.87M | 20% | Albert Laporta, Magic Mike 333, SusoGattuso |

### Ledger warnings

- BurtonGM89: net spend exceeds the 100M budget by 24.56M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

---

Sections 2 and 3 are hypotheses until the sample grows: with 18 ledger rows across 5 managers, a median is one or two deals. Section 1 and section 5 are usable today.

## Who to buy


Everyone not owned by the 5 of us, 60% start or better.

Filtered to what your 111.37M of cash can reach.

### portero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alvaro valles | Betis | por | 31.80M | 523K | 95% |
| antonio sivera | Alavés | por | 31.09M | 170K | 95% |
| david soria | Getafe | por | 18.03M | 14K | 95% |
| augusto batalla | Rayo | por | 41.01M | -530K | 95% |
| stole dimitrievski | Valencia | por | 13.28M | 370K | 90% |
| odysseas vlachodimos | Sevilla | por | 17.20M | 358K | 90% |
| mathew ryan | Levante | por | 13.55M | 0K | 90% |
| ionut radu | Celta | por | 40.06M | -255K | 90% |

### defensa

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| leandro cabrera | Espanyol | def | 16.61M | 71K | 95% |
| adrian de la fuente | Levante | def | 14.05M | 0K | 90% |
| jorge salinas | Racing | def | 6.02M | 0K | 90% |
| jon martin | Real Sociedad | def | 30.11M | 0K | 90% |
| nahuel tenaglia | Alavés | def | 14.34M | -23K | 90% |
| david affengruber | Elche | def | 33.40M | -34K | 90% |
| florian lejeune | Rayo | def | 38.17M | -144K | 90% |
| andrei ratiu | Rayo | def | 35.45M | -196K | 90% |

### mediocampista

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| jon ander olasagasti | Levante | med | 4.15M | 73K | 90% |
| javi guerra | Valencia | med | 23.86M | -46K | 90% |
| german valera | Elche | med | 23.33M | -110K | 90% |
| guido rodriguez | Valencia | med | 26.06M | -248K | 90% |
| david larrubia | Málaga | med | 56.06M | -266K | 90% |
| federico valverde | Real Madrid | med | 69.44M | -588K | 90% |
| alberto moleiro | Villarreal | med | 65.89M | 821K | 80% |
| sergio canales | Racing | med | 21.75M | 680K | 80% |

### delantero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| martin satriano | Getafe | del | 32.56M | 28.23M | 90% |
| vinicius junior | Real Madrid | del | 106.55M | 1.01M | 90% |
| ante budimir | Osasuna | del | 48.86M | 561K | 90% |
| jorge de frutos | Rayo | del | 47.70M | -886K | 90% |
| georges mikautadze | Villarreal | del | 61.15M | 793K | 80% |
| isi palazon | Rayo | del | 17.41M | 544K | 80% |
| ivan romero | Levante | del | 7.06M | 95K | 80% |
| angel perez | Alavés | del | 6.81M | -100K | 80% |

---

Not all of these are purchasable today — the app deals a limited slate. Paste today's slate into the `seen` input to mark which ones you can actually buy.

## Squad detail


| Manager | Players | Squad value | Spent | Raised | Cash |
|---|--:|--:|--:|--:|--:|
| **miguel_autentico** | 12 | 121.35M | 6.89M | 17.79M | 111.37M |
| Albert Laporta | 11 | 149.47M | 49.99M | 30.47M | ~80.48M |
| BurtonGM89 | 17 | 247.07M | 125.03M | 469K | — |
| Magic Mike 333 | 18 | 213.31M | 83.70M | 0K | ~16.30M |
| SusoGattuso | 14 | 114.28M | 0K | 0K | ~100.00M |

`~` is an estimate, not an observed balance — see the basis notes at the bottom. Cash is a ceiling on what anyone can bid tomorrow, which is the point of tracking it.

### You (miguel_autentico)
12 players · 121.35M total · 7 at 70%+ · cash 111.37M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alvaro fernandez | Deportivo | por | 4.87M | -94K | 20% |
| carl starfelt | Celta | def | 13.57M | 58K | 60% |
| robin le normand | Atlético | def | 9.83M | -105K | 60% |
| igor zubeldia | Real Sociedad | def | 9.76M | 30K | 80% |
| omar el hilali | Espanyol | def | 8.64M | 81K | 80% |
| ruben garcia | Osasuna | med | 13.41M | 148K | 60% |
| iñigo ruiz de galarreta | Athletic | med | 11.95M | 57K | 60% |
| dani lorenzo | Málaga | med | 9.55M | 38K | 90% |
| pepelu | Valencia | med | 7.40M | -60K | 70% |
| beñat turrientes | Real Sociedad | med | 6.92M | 56K | 70% |
| jon moncayola | Osasuna | med | 6.58M | 56K | 90% |
| iñigo vicente | Racing | del | 18.88M | 22K | 90% |

### Albert Laporta
11 players · 149.47M total · 2 at 70%+ · cash ~80.48M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| matias dituro | Elche | por | 7.16M | 132K | 90% |
| diego javier llorente | Betis | def | 12.94M | 99K | 60% |
| juan foyth | Villarreal | def | 11.46M | 235K | 50% |
| pedro bigas | Elche | def | 5.43M | -138K | 50% |
| eduardo camavinga | Real Madrid | med | 10.64M | -252K | 30% |
| ilaix moriba | Celta | med | 10.35M | -112K | 50% |
| marc roca | Betis | med | 5.27M | 38K | 60% |
| abde ezzalzouli | Betis | del | 47.47M | -1.23M | 30% |
| ayoze perez | Villarreal | del | 16.42M | 217K | 50% |
| raul moro | Osasuna | del | 13.06M | 77K | 60% |
| ferran jutgla | Celta | del | 9.27M | 27K | 80% |

### BurtonGM89
17 players · 247.07M total · 7 at 70%+ · cash —

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| marko dmitrovic | Espanyol | por | 37.09M | 257K | 95% |
| juan musso | Atlético | por | 4.67M | -106K | 10% |
| carlos romero | Villarreal | def | 42.29M | 692K | 80% |
| eder militao | Real Madrid | def | 12.78M | -381K | 0% |
| quilindschy hartman | Espanyol | def | 9.21M | -194K | 50% |
| justin de haas | Valencia | def | 8.87M | 99K | 70% |
| carlos puga | Málaga | def | 5.34M | -57K | 70% |
| giacomo quagliata | Deportivo | def | 3.29M | -7K | 50% |
| santi comesaña | Villarreal | med | 32.99M | 684K | 90% |
| tajon buchanan | Villarreal | med | 18.67M | -244K | 30% |
| antonio blanco | Alavés | med | 14.96M | -86K | 90% |
| jon gorrotxategi | Real Sociedad | med | 6.72M | -195K | 30% |
| unai lopez | Rayo | med | 5.77M | -2K | 70% |
| aliou dieng | Valencia | med | 4.90M | -105K | 40% |
| karl etta eyong | Levante | del | 25.19M | 264K | 50% |
| iago aspas | Celta | del | 7.34M | -119K | 40% |
| joaquin muñoz | Málaga | del | 6.97M | -116K | 60% |

### Magic Mike 333
18 players · 213.31M total · 6 at 70%+ · cash ~16.30M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alfonso herrero | Málaga | por | 7.95M | 116K | 80% |
| lucas noubi | Deportivo | def | 11.79M | 237K | 80% |
| kike salas | Sevilla | def | 11.76M | 62K | 90% |
| jose gaya | Valencia | def | 11.15M | -118K | 60% |
| raul asencio | Real Madrid | def | 4.86M | -156K | 0% |
| fabio cardoso | Sevilla | def | 879K | -23K | 0% |
| alex pastor | Málaga | def | 382K | -3K | 40% |
| gustavo puerta | Racing | med | 12.32M | 135K | 80% |
| brahim diaz | Real Madrid | med | 10.58M | -243K | 50% |
| gabriel moscardo | Espanyol | med | 9.88M | -242K | 30% |
| williot swedberg | Celta | med | 8.10M | -70K | 50% |
| marc bernal | Barcelona | med | 6.35M | 257K | 60% |
| pedro diaz | Rayo | med | 1.83M | -35K | 50% |
| raphinha | Barcelona | del | 79.07M | 2.28M | 70% |
| gorka guruzeta | Athletic | del | 13.36M | 119K | 80% |
| lucas boye | Alavés | del | 12.81M | -335K | 40% |
| pere milla | Espanyol | del | 9.66M | -104K | 50% |
| jon karrikaburu | Real Sociedad | del | 592K | -12K | 0% |

### SusoGattuso
14 players · 114.28M total · 9 at 70%+ · cash ~100.00M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| wojciech szczesny | Barcelona | por | 2.45M | -53K | 20% |
| yuri berchiche | Athletic | def | 12.59M | -96K | 70% |
| cesar tarrega | Valencia | def | 8.87M | -0K | 80% |
| abdel abqar | Getafe | def | 6.75M | 123K | 70% |
| jonny castro | Alavés | def | 5.46M | -33K | 70% |
| alvaro garcia | Villarreal | def | 502K | 0K | 80% |
| aleksa puric | Atlético | def | 435K | -0K | — |
| aimar oroz | Osasuna | med | 15.60M | 75K | 70% |
| lorenzo amatucci | Deportivo | med | 12.30M | 130K | 80% |
| izan merino | Málaga | med | 6.49M | -72K | 70% |
| johnny cardoso | Atlético | med | 6.04M | -131K | 30% |
| andres martin | Racing | del | 19.35M | -167K | 80% |
| alex berenguer | Athletic | del | 9.09M | -127K | 30% |
| carlos espi | Real Madrid | del | 8.36M | 160K | 30% |

### What they pay

| Date | Player | From → To | Price |
|---|---|---|--:|
| 2026-08-11T21:24 | giacomo quagliata | market → BurtonGM89 | 4000000 |
| 2026-08-11T21:24 | carlos romero | market → BurtonGM89 | 45739000 |
| 2026-08-11T21:24 | fabio cardoso | market → Magic Mike 333 | 949269 |
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

### Cash basis

- **miguel_autentico** — balance you recorded on 2026-08-12, then 3 ledger row(s) (known)
- **Albert Laporta** — 100M starting budget, then 5 ledger row(s) (estimated)
- **BurtonGM89** — 100M starting budget, then 5 ledger row(s), which overdraws it by 24.56M (unknown)
- **Magic Mike 333** — 100M starting budget, then 4 ledger row(s) (estimated)
- **SusoGattuso** — 100M starting budget, then 0 ledger row(s) (estimated)
