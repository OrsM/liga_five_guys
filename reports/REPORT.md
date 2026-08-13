# Liga Five Guys — one report — 2026-08-13 10:56 UTC

Everything the generators produced, in reading order. Sections that appeared twice are printed once.

- Decide today
- Rivals — cash, premiums, squads
- Who to buy
- On offer now
- Bid log
- Squad detail

## Decide today


### Needs a decision

- **Only 1 portero** — one knock and you can't field a legal XI.
- **3 of the XI are unmodelled** (Alvaro Fernandez, Dani Lorenzo, Iñigo Vicente) — no LaLiga record, so they're carrying an assumed baseline, not an earned one.

**Squad 138.65M** · cash 93.58M · total 232.23M

Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger.

### Team

**4-5-1** · index 29.2 (a ranking number, not a points forecast)

| | Player | Start% | Value | 24h | Score | Last season |
|---|---|--:|--:|--:|--:|---|
| POR | Alvaro Fernandez | 20% | 4.88M | -86K | 0.9 | **assumed** |
| DEF | Carl Starfelt | 60% | 13.62M | 105K | 3.1 | 106p/19j |
| DEF | Igor Zubeldia | 80% | 9.79M | 61K | 3.0 | 90p/25j |
| DEF | Robin Le Normand | 60% | 9.85M | -82K | 2.6 | 125p/28j |
| DEF | Omar El Hilali | 80% | 8.67M | 113K | 2.4 | 101p/36j |
| MED | Jon Moncayola | 90% | 6.60M | 80K | 3.9 | 159p/36j |
| MED | Ruben Garcia | 60% | 13.46M | 200K | 2.9 | 174p/35j |
| MED | Iñigo Ruiz de Galarreta | 60% | 11.99M | 98K | 2.7 | 156p/34j |
| MED | Dani Lorenzo | 90% | 9.58M | 70K | 2.6 | **assumed** |
| MED | Beñat Turrientes | 70% | 6.94M | 81K | 2.5 | 93p/27j |
| DEL | Iñigo Vicente | 90% | 18.94M | 82K | 2.7 | **assumed** |

**Bench** — gap is what the XI index loses by playing him instead, after re-picking the formation. €/pt is his value per point of score: the sell shortlist, worst first.

| Player | Pos | Value | Score | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Orri Steinn Oskarsson | del | 8.00M | 1.7 | -0.7 | 4.69M | outscored |
| Hugo Duro | del | 8.49M | 1.9 | -0.5 | 4.44M | outscored |
| Pepelu | med | 7.41M | 2.1 | -0.4 | 3.45M | 6th MED — only 5 can ever play |
| Dani Martinez | def | 412K | 0.5 | -1.9 | 794K | outscored |

_A sale lands above or below value depending on who bids. Who is short in this position, and who can still afford you, is in `reports/behaviour.md`._

### Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Ruben Garcia | 13.46M | 200K | +1.51% |
| Omar El Hilali | 8.67M | 113K | +1.32% |
| Jon Moncayola | 6.60M | 80K | +1.23% |
| Beñat Turrientes | 6.94M | 81K | +1.18% |
| Orri Steinn Oskarsson | 8.00M | -128K | -1.57% |
| Alvaro Fernandez | 4.88M | -86K | -1.73% |

---

_611 players tracked, 508 with a probable-XI reading. Who to buy is in `reports/watchlist.md`; how your rivals bid is in `reports/behaviour.md`._

_Score = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Recommended XIs are logged to `data/decisions/squad_log.csv` for scoring against reality later._

_Generated 2026-08-13 10:56 UTC._

## Rivals — cash, premiums, squads


5 managers, 15 ledger rows, 17 market snapshots, points baseline 2025-26.

### 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 15 | 6.89M | 0K | -6.89M | 93.58M | 93.58M |
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
| **miguel_autentico** | 29.2 | 4-5-1 | 13.29M | 0 | por | 0 |
| Albert Laporta | **illegal** | — | 58.19M | 0 | por,def,med | 0 |
| BurtonGM89 | 34.2 | 4-4-2 | 50.26M | 0 | — | 0 |
| Magic Mike 333 | 33.3 | 3-4-3 | 29.44M | 0 | por | 0 |
| SusoGattuso | 27.1 | 4-4-2 | 26.43M | 0 | por | 0 |

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
| joan garcia | POR | 66.34M | 80% | Albert Laporta, Magic Mike 333, SusoGattuso |
| alvaro valles | POR | 31.94M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| federico valverde | MED | 69.62M | 90% | Albert Laporta |
| david soria | POR | 18.09M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| jan oblak | POR | 53.53M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| augusto batalla | POR | 41.10M | 95% | Albert Laporta, Magic Mike 333, SusoGattuso |
| ionut radu | POR | 40.17M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| zaid romero | DEF | 30.12M | 90% | Albert Laporta |
| mathew ryan | POR | 13.55M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |
| florian lejeune | DEF | 38.27M | 90% | Albert Laporta |
| pablo fornals | MED | 58.90M | 80% | Albert Laporta |
| stole dimitrievski | POR | 13.35M | 90% | Albert Laporta, Magic Mike 333, SusoGattuso |

**Nobody else needs these.** Same quality, no auction — take the equivalent player here instead of paying a premium above.

| Player | Pos | Value | Start% |
|---|---|--:|--:|
| kylian mbappe | DEL | 130.88M | 70% |
| vinicius junior | DEL | 106.94M | 90% |
| lamine yamal | DEL | 128.10M | 60% |
| ante budimir | DEL | 49.05M | 90% |
| martin satriano | DEL | 4.34M | 90% |
| georges mikautadze | DEL | 61.39M | 80% |
| jorge de frutos | DEL | 47.78M | 90% |
| nicolas pepe | DEL | 49.29M | 70% |

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | Start% | Short |
|---|---|--:|--:|---|
| alvaro fernandez | POR | 4.88M | 20% | Albert Laporta, Magic Mike 333, SusoGattuso |
| dani martinez | DEF | 412K | — | Albert Laporta |

### Ledger warnings

- BurtonGM89: net spend exceeds the 100M budget by 24.56M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

---

Sections 2 and 3 are hypotheses until the sample grows: with 15 ledger rows across 5 managers, a median is one or two deals. Section 1 and section 5 are usable today.

## Who to buy


Everyone not owned by the 5 of us, 60% start or better.

Filtered to what your 93.58M of cash can reach.

### portero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alvaro valles | Betis | por | 31.94M | 660K | 95% |
| antonio sivera | Alavés | por | 31.20M | 278K | 95% |
| david soria | Getafe | por | 18.09M | 70K | 95% |
| augusto batalla | Rayo | por | 41.10M | -443K | 95% |
| stole dimitrievski | Valencia | por | 13.35M | 439K | 90% |
| odysseas vlachodimos | Sevilla | por | 17.28M | 437K | 90% |
| mathew ryan | Levante | por | 13.55M | 0K | 90% |
| ionut radu | Celta | por | 40.17M | -150K | 90% |

### defensa

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| leandro cabrera | Espanyol | def | 16.67M | 128K | 95% |
| david affengruber | Elche | def | 33.50M | 67K | 90% |
| nahuel tenaglia | Alavés | def | 14.39M | 19K | 90% |
| adrian de la fuente | Levante | def | 14.05M | 0K | 90% |
| jorge salinas | Racing | def | 6.02M | 0K | 90% |
| zaid romero | Getafe | def | 30.12M | 0K | 90% |
| jon martin | Real Sociedad | def | 30.11M | 0K | 90% |
| florian lejeune | Rayo | def | 38.27M | -38K | 90% |

### mediocampista

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| jon ander olasagasti | Levante | med | 4.17M | 91K | 90% |
| javi guerra | Valencia | med | 23.93M | 24K | 90% |
| german valera | Elche | med | 23.39M | -46K | 90% |
| david larrubia | Málaga | med | 56.22M | -113K | 90% |
| guido rodriguez | Valencia | med | 26.12M | -186K | 90% |
| federico valverde | Real Madrid | med | 69.62M | -417K | 90% |
| alberto moleiro | Villarreal | med | 66.15M | 1.08M | 80% |
| sergio canales | Racing | med | 21.86M | 797K | 80% |

### delantero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| ante budimir | Osasuna | del | 49.05M | 750K | 90% |
| martin satriano | Getafe | del | 4.34M | 0K | 90% |
| jorge de frutos | Rayo | del | 47.78M | -804K | 90% |
| georges mikautadze | Villarreal | del | 61.39M | 1.04M | 80% |
| isi palazon | Rayo | del | 17.50M | 638K | 80% |
| ivan romero | Levante | del | 7.09M | 124K | 80% |
| toni martinez | Alavés | del | 25.20M | -42K | 80% |
| angel perez | Alavés | del | 6.82M | -87K | 80% |

---

Not all of these are purchasable today — the app deals a limited slate. This is the shortlist to recognise against.

## On offer now


Ranked by start probability, then price edge. **Edge** is value minus asking price (or the 24h move if you didn't give a price): positive means you pay less than today's value.

| Player | Team | Pos | Value | Ask | Edge | 24h | Start% | |
|---|---|---|--:|--:|--:|--:|--:|---|
| marko dmitrovic | Espanyol | por | 37.22M | — | 389K | 389K | 95% |  |
| santi comesaña | Villarreal | med | 33.14M | — | 835K | 835K | 90% |  |
| ferran jutgla | Celta | del | 9.31M | — | 58K | 58K | 80% |  |
| ademola lookman | Atlético | del | 78.93M | — | -261K | -261K | 80% |  |
| raphinha | Barcelona | del | 79.48M | — | 2.70M | 2.70M | 70% |  |
| juan foyth | Villarreal | def | 11.51M | — | 288K | 288K | 50% |  |
| ayoze perez | Villarreal | del | 16.49M | — | 282K | 282K | 50% |  |
| pedro diaz | Rayo | med | 1.83M | — | -32K | -32K | 50% |  |
| pathe ciss | Rayo | med | 11.75M | — | 147K | 147K | 40% |  |
| andre almeida | Valencia | med | 2.57M | — | -55K | -55K | 30% |  |
| abde ezzalzouli | Betis | del | 47.53M | — | -1.18M | -1.18M | 30% |  |
| dani vivian | Athletic | def | 11.49M | — | -356K | -356K | 30% |  |
| giorgi guliashvili | Racing | del | 2.22M | — | -72K | -72K | 20% |  |
| alvaro djalo | Athletic | del | 1.12M | — | -29K | -29K | 10% |  |
| pablo duran | Celta | del | 1.10M | — | -32K | -32K | 10% |  |
| unai egiluz | Athletic | def | 426K | — | -4K | -4K | 0% |  |
| jon karrikaburu | Real Sociedad | del | 593K | — | -11K | -11K | 0% |  |
| javi puado | Espanyol | med | 6.88M | — | -219K | -219K | 0% |  |
| martin anselmi | Elche | ent | 1.78M | — | -60K | -60K | — |  |
| pellegrino matarazzo | Real Sociedad | ent | 4.30M | — | -158K | -158K | — |  |

**Unresolved:**

- **Manuel Ferná...** — no match

---

No expected-points model yet, so this ranks on start probability and price only. A 90% starter at a weak club still scores less than a 70% starter at a strong one — use judgement.

_Generated 2026-08-13 10:56 UTC._

## Bid log


2 bids, 0 settled, 0 won.

_Nothing settled yet. Set `outcome` to won/lost/outbid as auctions resolve._

| Date | Player | Bid | Value | Premium | Outcome | Bids |
|---|---|--:|--:|--:|---|--:|
| 2026-08-11 | beñat turrientes | 6.89M | 6.79M | 1.47% | pending | 1 |
| 2026-08-11 | ferran jutgla | 9.30M | 9.12M | 1.97% | pending | 1 |

---

Record every auction, including losses — a loss at a known premium is what tells you where rivals actually sit.

`~` marks a value read from a snapshot more than 36h from the bid.

_Generated 2026-08-13 10:56 UTC._

## Squad detail


| Manager | Players | Squad value | Spent | Raised | Cash |
|---|--:|--:|--:|--:|--:|
| **miguel_autentico** | 15 | 138.65M | 6.89M | 0K | 93.58M |
| Albert Laporta | 11 | 149.86M | 49.99M | 30.47M | ~80.48M |
| BurtonGM89 | 17 | 247.86M | 125.03M | 469K | — |
| Magic Mike 333 | 18 | 214.11M | 83.70M | 0K | ~16.30M |
| SusoGattuso | 14 | 114.61M | 0K | 0K | ~100.00M |

`~` is an estimate, not an observed balance — see the basis notes at the bottom. Cash is a ceiling on what anyone can bid tomorrow, which is the point of tracking it.

### You (miguel_autentico)
15 players · 138.65M total · 6 at 70%+ · cash 93.58M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alvaro fernandez | Deportivo | por | 4.88M | -86K | 20% |
| carl starfelt | Celta | def | 13.62M | 105K | 60% |
| robin le normand | Atlético | def | 9.85M | -82K | 60% |
| igor zubeldia | Real Sociedad | def | 9.79M | 61K | 80% |
| omar el hilali | Espanyol | def | 8.67M | 113K | 80% |
| dani martinez | Atlético | def | 412K | -3K | — |
| ruben garcia | Osasuna | med | 13.46M | 200K | 60% |
| iñigo ruiz de galarreta | Athletic | med | 11.99M | 98K | 60% |
| dani lorenzo | Málaga | med | 9.58M | 70K | 90% |
| pepelu | Valencia | med | 7.41M | -41K | 50% |
| beñat turrientes | Real Sociedad | med | 6.94M | 81K | 70% |
| jon moncayola | Osasuna | med | 6.60M | 80K | 90% |
| iñigo vicente | Racing | del | 18.94M | 82K | 90% |
| hugo duro | Valencia | del | 8.49M | -45K | 50% |
| orri steinn oskarsson | Real Sociedad | del | 8.00M | -128K | 40% |

### Albert Laporta
11 players · 149.86M total · 2 at 70%+ · cash ~80.48M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| matias dituro | Elche | por | 7.19M | 164K | 90% |
| diego javier llorente | Betis | def | 12.99M | 146K | 60% |
| juan foyth | Villarreal | def | 11.51M | 288K | 50% |
| pedro bigas | Elche | def | 5.43M | -132K | 50% |
| eduardo camavinga | Real Madrid | med | 10.66M | -238K | 30% |
| ilaix moriba | Celta | med | 10.37M | -89K | 50% |
| marc roca | Betis | med | 5.29M | 57K | 60% |
| abde ezzalzouli | Betis | del | 47.53M | -1.18M | 30% |
| ayoze perez | Villarreal | del | 16.49M | 282K | 50% |
| raul moro | Osasuna | del | 13.10M | 123K | 60% |
| ferran jutgla | Celta | del | 9.31M | 58K | 80% |

### BurtonGM89
17 players · 247.86M total · 7 at 70%+ · cash —

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| marko dmitrovic | Espanyol | por | 37.22M | 389K | 95% |
| juan musso | Atlético | por | 4.68M | -100K | 10% |
| carlos romero | Villarreal | def | 42.47M | 874K | 80% |
| eder militao | Real Madrid | def | 12.79M | -370K | 0% |
| quilindschy hartman | Espanyol | def | 9.22M | -179K | 50% |
| justin de haas | Valencia | def | 8.90M | 134K | 70% |
| carlos puga | Málaga | def | 5.36M | -45K | 70% |
| giacomo quagliata | Deportivo | def | 3.30M | 3K | 50% |
| santi comesaña | Villarreal | med | 33.14M | 835K | 90% |
| tajon buchanan | Villarreal | med | 18.71M | -204K | 30% |
| antonio blanco | Alavés | med | 15.00M | -46K | 90% |
| jon gorrotxategi | Real Sociedad | med | 6.72M | -189K | 30% |
| unai lopez | Rayo | med | 5.79M | 16K | 70% |
| aliou dieng | Valencia | med | 4.91M | -98K | 50% |
| karl etta eyong | Levante | del | 25.29M | 361K | 50% |
| iago aspas | Celta | del | 7.35M | -105K | 40% |
| joaquin muñoz | Málaga | del | 6.99M | -103K | 60% |

### Magic Mike 333
18 players · 214.11M total · 7 at 70%+ · cash ~16.30M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alfonso herrero | Málaga | por | 7.98M | 149K | 80% |
| lucas noubi | Deportivo | def | 11.84M | 290K | 80% |
| kike salas | Sevilla | def | 11.80M | 103K | 90% |
| jose gaya | Valencia | def | 11.17M | -93K | 80% |
| raul asencio | Real Madrid | def | 4.86M | -152K | 0% |
| fabio cardoso | Sevilla | def | 880K | -22K | 0% |
| alex pastor | Málaga | def | 383K | -2K | 40% |
| gustavo puerta | Racing | med | 12.37M | 183K | 80% |
| brahim diaz | Real Madrid | med | 10.59M | -228K | 50% |
| gabriel moscardo | Espanyol | med | 9.90M | -229K | 30% |
| williot swedberg | Celta | med | 8.12M | -50K | 50% |
| marc bernal | Barcelona | med | 6.39M | 296K | 60% |
| pedro diaz | Rayo | med | 1.83M | -32K | 50% |
| raphinha | Barcelona | del | 79.48M | 2.70M | 70% |
| gorka guruzeta | Athletic | del | 13.41M | 169K | 80% |
| lucas boye | Alavés | del | 12.82M | -321K | 40% |
| pere milla | Espanyol | del | 9.68M | -82K | 50% |
| jon karrikaburu | Real Sociedad | del | 593K | -11K | 0% |

### SusoGattuso
14 players · 114.61M total · 9 at 70%+ · cash ~100.00M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| wojciech szczesny | Barcelona | por | 2.45M | -49K | 20% |
| yuri berchiche | Athletic | def | 12.63M | -65K | 70% |
| cesar tarrega | Valencia | def | 8.90M | 27K | 80% |
| abdel abqar | Getafe | def | 6.78M | 152K | 70% |
| jonny castro | Alavés | def | 5.48M | -19K | 70% |
| alvaro garcia | Alavés | def | 502K | 0K | 80% |
| aleksa puric | Atlético | def | 431K | -4K | — |
| aimar oroz | Osasuna | med | 15.66M | 128K | 70% |
| lorenzo amatucci | Deportivo | med | 12.35M | 178K | 80% |
| izan merino | Málaga | med | 6.50M | -57K | 70% |
| johnny cardoso | Atlético | med | 6.04M | -122K | 30% |
| andres martin | Racing | del | 19.39M | -120K | 80% |
| alex berenguer | Athletic | del | 9.10M | -108K | 30% |
| carlos espi | Real Madrid | del | 8.40M | 198K | 30% |

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

### Cash basis

- **miguel_autentico** — balance you recorded on 2026-08-12, then 0 ledger row(s) (known)
- **Albert Laporta** — 100M starting budget, then 5 ledger row(s) (estimated)
- **BurtonGM89** — 100M starting budget, then 5 ledger row(s), which overdraws it by 24.56M (unknown)
- **Magic Mike 333** — 100M starting budget, then 4 ledger row(s) (estimated)
- **SusoGattuso** — 100M starting budget, then 0 ledger row(s) (estimated)
