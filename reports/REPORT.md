# Liga Five Guys — one report — 2026-08-15 08:33 UTC

Everything the generators produced, in reading order. Sections that appeared twice are printed once.

- Decide today
- Rivals — cash, premiums, squads
- Who to buy
- Squad detail
- How the forecast works — and how it's doing

## Decide today


### Needs a decision

- **9 players on offer, 2 improve your XI** — priced in the slate table below.
- **Locks in 8h.** Probable XIs are at their most reliable now.
- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 of the XI are unmodelled** (Iñigo Vicente) — no LaLiga record, so they're carrying an assumed baseline, not an earned one.
- **Your bench costs you 1.1 pts/j** — marked XI ≈34, best ≈35. Swap Beñat Turrientes → Lucien Agoume (+0.6); Dani Lorenzo → Pepelu (+0.4).

**Squad 171.50M** · cash 63.29M · total 234.79M

Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger.

### Today's slate — 9 on offer, 2 improve your XI

| Player | Pos | Value | Start% | xPts/j | XI gain | Bid | Competition | Verdict |
|---|---|--:|--:|--:|--:|--:|---|---|
| Pablo Fornals | med | 57.93M | 80% | 5.4 | +2.7 | 59.46M–63.29M | Magic +3.2?, (3 broke) | **Bid** — XI +2.7 |
| Santiago Mouriño | def | 40.48M | 70% | 3.1 | +0.7 | 41.55M–49.22M | Magic +0.9?, (3 broke) | **Bid** — XI +0.7 |
| Mario Martin | med | 3.83M | 80% | 2.3 | -0.4 | 3.93M–4.66M | Albert +0.4, +2 more, (1 broke) | pass — XI -0.4 |
| Pelayo Fernandez | def | 1.72M | 50% | 1.9 | -0.5 | 1.77M–2.09M | BurtonGM +0.5 | pass — XI -0.5 |
| Clemens Riedel | def | 4.60M | 50% | 1.9 | -0.5 | 4.72M–5.59M | (1 broke) | pass — XI -0.5 |
| Oriol Rey | med | 734K | 50% | 1.7 | -1.0 | 754K–893K | BurtonGM +0.4 | pass — XI -1.0 |
| Dani Vivian | def | 10.82M | 30% | 1.2 | -1.2 | 11.11M–13.16M | none | pass — XI -1.2 |
| Alex Freeman | def | 2.45M | 30% | 1.0 | -1.4 | 2.51M–2.97M | none | pass — XI -1.4 |
| Zakaria Eddahchouri | del | 1.88M | 30% | 0.9 | -1.5 | 1.93M–2.29M | none | pass — XI -1.5 |

Competition is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. The full manager-by-manager matrix is in `reports/behaviour.md`.

Bid is the floor plus what this league has actually paid over it: median +2.6%, -0.2% to +21.6% (n=21). 6 of those 21 went at the floor itself, so the minimum is not a number known to lose. A dozen deals is not a distribution — the range is what has happened, not a chance of winning, and every one of them is a bid that won.

_At least one rival's cash is an estimate, so no bid here assumes you are unopposed._

Already owned, so not a purchase: arda guler (Albert Laporta), ayoze perez (Albert Laporta), ferran jutgla (Albert Laporta), giacomo quagliata (BurtonGM89), ilaix moriba (Albert Laporta), marc roca (Albert Laporta), matias dituro (Albert Laporta), pedro bigas (Albert Laporta), quilindschy hartman (BurtonGM89), wojciech szczesny (SusoGattuso).

### Names I could not place

OCR mangled these past matching, so they are missing from the table above — re-read them off the app if one matters.

- **Bright Ede** — no match
- **Buonanotte** — no match

### Team

**4-5-1** · **≈35 pts expected next jornada** (uncalibrated — see *How the forecast works* at the end)

| | Player | Start% | Value | 24h | xPts/j | Last season |
|---|---|--:|--:|--:|--:|---|
| POR | Ionut Radu | 90% | 39.25M | -464K | 5.9 | 254p/38j |
| DEF | Carl Starfelt | 60% | 13.85M | 70K | 3.1 | 106p/19j |
| DEF | Igor Zubeldia | 80% | 9.94M | 78K | 3.0 | 90p/25j |
| DEF | Robin Le Normand | 60% | 9.98M | 140K | 2.6 | 125p/28j |
| DEF | Omar El Hilali | 80% | 8.84M | 61K | 2.4 | 101p/36j |
| MED | Jon Moncayola | 90% | 6.79M | 107K | 3.9 | 159p/36j |
| MED | Lucien Agoume | 80% | 5.82M | 28K | 3.2 | 133p/34j |
| MED | Pepelu | 70% | 7.47M | 99K | 3.0 | 135p/31j |
| MED | Ruben Garcia | 60% | 13.75M | 173K | 2.9 | 174p/35j |
| MED | Iñigo Ruiz de Galarreta | 60% | 12.24M | 60K | 2.7 | 156p/34j |
| DEL | Iñigo Vicente | 90% | 19.17M | 75K | 2.7 | **assumed** |

_As marked in `inputs/lineup.txt`: **≈34 pts** — 1.1 below this XI. The table above is the recommendation, not what you are fielding._

**Bench** — gap is the expected points the XI loses per jornada by playing him instead, after re-picking the formation. €/pt is his value per expected point: the sell shortlist, worst first.

| Player | Pos | Value | xPts/j | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Alvaro Fernandez | por | 4.73M | 0.9 | -5.1 | 5.42M | 3rd POR — only 1 can ever play |
| Dani Lorenzo | med | 9.69M | 2.6 | -0.1 | 3.79M | 6th MED — only 5 can ever play |
| Beñat Turrientes | med | 7.06M | 2.5 | -0.2 | 2.81M | 7th MED — only 5 can ever play |
| Simon Eriksson | por | 2.92M | 2.2 | -3.8 | 1.34M | 2nd POR — only 1 can ever play |

_Selling to the app pays the value give or take 12%: the 13 priced sales in the ledger went median +3.3%, -9.4% to +12.0% (n=13), so read every value above as that band, not as a number. Who is short in this position, and who can still afford you, is in `reports/behaviour.md`._

### Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Jon Moncayola | 6.79M | 107K | +1.61% |
| Robin Le Normand | 9.98M | 140K | +1.43% |
| Pepelu | 7.47M | 99K | +1.34% |
| Ruben Garcia | 13.75M | 173K | +1.27% |
| Beñat Turrientes | 7.06M | 78K | +1.12% |
| Ionut Radu | 39.25M | -464K | -1.17% |
| Alvaro Fernandez | 4.73M | -68K | -1.42% |

---

_621 players tracked, 512 with a probable-XI reading. Who to buy is in `reports/watchlist.md`; how your rivals bid is in `reports/behaviour.md`._

_xPts/j — expected points per jornada = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. How it works, what it ignores, and how it is tracking reality: *How the forecast works* at the end of this report._

_Generated 2026-08-15 08:33 UTC._

## Rivals — cash, premiums, squads


5 managers, 35 ledger rows, 29 market snapshots, points baseline 2025-26.

### 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 15 | 55.37M | 17.79M | -37.58M | 63.29M | 63.29M |
| Albert Laporta | 14 | 125.28M | 30.47M | -94.81M | ~5.19M | 5.19M |
| BurtonGM89 | 13 | 127.61M | 30.03M | -97.58M | ~2.42M | 2.42M |
| Magic Mike 333 | 19 | 121.23M | 14.01M | -107.22M | — | — |
| SusoGattuso | 16 | 62.44M | 0K | -62.44M | ~37.56M | 37.56M |

`~` is an estimate: the starting budget less every ledger row, not an observed balance. The starting squad was dealt free, so it costs nothing here. A `—` means the ledger overdraws the budget, so the number would be fiction — see the warnings. Any time a rival mentions a balance, put it in `inputs/cash.txt` — one observed number turns their whole estimate into arithmetic.

**Cash-constrained right now:** BurtonGM89 (2.42M). Against these, open at the minimum increment — they cannot escalate.

### 2. What they pay over value

| Manager | Buys | Median premium | Range | Round bids |
|---|--:|--:|---|--:|
| miguel_autentico | 4 | +1.5% | -0.1% to +1.7% | 0/4 |
| Albert Laporta | 4 | +2.6% | +0.0% to +5.0% | 0/4 |
| BurtonGM89 | 5 | +9.2% | +0.0% to +21.6% | 2/5 |
| Magic Mike 333 | 6 | +6.3% | +2.4% to +15.9% | 4/6 |
| SusoGattuso | 2 | +3.6% | -0.2% to +3.6% | 0/2 |

**The floor sometimes wins.** 6 of the 21 priced purchases in this league went at the market value itself and the other 15 cleared it, median +2.6%, -0.2% to +21.6% (n=21) across all of them. Bidding the minimum is therefore not the one number known to lose — but 6 of 21 is a share of the bids that WON, not the odds of winning one. Nothing in this ledger records a bid that lost, so the floor's failure rate is unmeasured and unmeasurable from here.

**The app does not pay you the value — it randomises around it.** The 13 priced sales back to the market went for median +3.3%, -9.4% to +12.0% (n=13): 5 below the value and 8 above, never further than 12.0% either way. So a sale raises the value give or take a tenth, and the value is not the money you will get. Whether the same randomiser bids against you for a free agent is inferred, not measured: every row in this ledger is a bid that won.

A round bid was typed by a human. That is the whole of what roundness tells you — an exact bid is *not* the app's valuation and does not mean nobody competed, because the premium column two cells left already measures how far above the floor the buyer went. Sealed bids are paid as bid, so a purchase at exactly the value was only ever yours to take if the tie-break favoured you, and that rule is not documented anywhere we can read. Check it in-app before reading a floor purchase as a bargain you missed.

| Date | Player | Buyer | Paid | Value then | Premium | Bid |
|---|---|---|--:|--:|--:|---|
| 08-14T21:24 | ionut radu | miguel_autentico | 39.66M | 39.71M | -0.1% | exact |
| 08-14T21:24 | agoume | miguel_autentico | 5.89M | 5.79M | +1.7% | exact |
| 08-14T21:24 | eriksson | miguel_autentico | 2.93M | 2.93M | +0.0% | exact |
| 08-14T21:24 | gavi | Magic Mike 333 | 35.50M | 33.40M | +6.3% | round |
| 08-14T21:24 | t. morente | Magic Mike 333 | 2.03M | 1.99M | +2.4% | exact |
| 08-14T21:24 | aramburu | SusoGattuso | 17.79M | 17.18M | +3.6% | exact |
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

| Date | Player | Actor | Side | +3d | +7d | +14d |
|---|---|---|---|--:|--:|--:|
| 08-11T22:26 | javi puado | Albert Laporta | sell | -9.1% | — | — |
| 08-11T22:25 | pathe ciss | Albert Laporta | sell | +3.3% | — | — |
| 08-11T22:24 | dani vivian | Albert Laporta | sell | -8.8% | — | — |
| 08-11T21:42 | unai egiluz | BurtonGM89 | sell | -3.0% | — | — |
| 08-11T21:24 | giacomo quagliata | BurtonGM89 | buy | -0.1% | — | — |
| 08-11T21:24 | carlos romero | BurtonGM89 | buy | +6.5% | — | — |
| 08-11T21:24 | fabio cardoso | Magic Mike 333 | buy | -7.0% | — | — |
| 08-11T21:24 | beñat turrientes | miguel_autentico | buy | +2.8% | — | — |

Two errors this table is built to catch: buying a player who has already risen (paying the top of the move), and selling one who has just dipped (realising the bottom). Both show as the drift column reversing sign against the actor.

### 4. Squad diagnostics

| Manager | xPts/j | Shape | Trapped | Injured | Thin at | Unmatched |
|---|--:|---|--:|--:|---|--:|
| **miguel_autentico** | 35.4 | 4-5-1 | 4.73M | 0 | del | 0 |
| Albert Laporta | 33.6 | 4-3-3 | 55.61M | 0 | por | 0 |
| BurtonGM89 | 31.4 | 5-4-1 | 36.79M | 0 | por,del | 0 |
| Magic Mike 333 | 32.4 | 3-4-3 | 17.75M | 0 | por | 0 |
| SusoGattuso | 29.4 | 4-4-2 | 26.31M | 0 | por | 0 |

- miguel_autentico is carrying more portero/mediocampista than can ever start.
- Albert Laporta is carrying more delantero than can ever start.
- BurtonGM89 is carrying more defensa than can ever start.
- Magic Mike 333 is carrying more defensa/mediocampista/delantero than can ever start.
- SusoGattuso is carrying more delantero/defensa than can ever start.

Trapped is value held in players below 50% start probability — money that cannot score. Unmatched is names in their squad missing from data/tidy, which are absent from the xPts total, so a large number there means the comparison flatters you.

### 5. Who wants what

Restricted to the 9 players on today's slate — the rest of the market is not a decision you can make today.

**The slate through every manager's eyes.** XI gain per jornada if that manager owned him, under the one shared scorer. `(…)` = wants him but their estimated cash cannot pay the floor. `?` = wants him, cash unknown — treat as live. **needs** = they cannot field a legal XI without buying in this position — a forced buyer. Your column has no cash cap: you know your own balance.

| Player | Pos | Value | You | Albert | BurtonGM | Magic | SusoGatt |
|---|---|--:|--:|--:|--:|--:|--:|
| pablo fornals | MED | 57.93M | +2.7 | (+3.5) | (+4.1) | +3.2? | (+3.4) |
| santiago mouriño | DEF | 40.48M | +0.7 | (+1.0) | (+1.7) | +0.9? | (+1.1) |
| mario martin | MED | 3.83M | -0.4 | +0.4 | (+1.0) | +0.1? | +0.3 |
| clemens riedel | DEF | 4.60M | -0.5 | -0.2 | (+0.5) | -0.3? | -0.1 |
| pelayo fernandez | DEF | 1.72M | -0.5 | -0.2 | +0.5 | -0.3? | -0.0 |
| oriol rey | MED | 734K | -1.0 | -0.3 | +0.4 | -0.5? | -0.3 |
| dani vivian | DEF | 10.82M | -1.2 | (-0.9) | (-0.2) | -1.0? | -0.8 |
| alex freeman | DEF | 2.45M | -1.4 | -1.1 | (-0.4) | -1.2? | -1.0 |
| zakaria eddahchouri | DEL | 1.88M | -1.5 | -1.2 | -0.4 | -1.3? | -1.1 |

Read it as an auction map: a player whose gain is big only in YOUR column is a quiet buy at the floor; big in a funded rival's column too means price the bid off their premium in section 2, or walk.

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | Start% | Short |
|---|---|--:|--:|---|
| alvaro fernandez | POR | 4.73M | 20% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

### 6. Projected XIs

Each manager's best legal XI under the same scorer — what a rational version of them fields. Once jornadas run, their actual points versus this forecast measures two things at once: the model's calibration (5× the sample your own squad gives), and who manages actively versus who set-and-forgets — a leak worth knowing at deal time.

| Manager | ≈pts/j | vs you | Shape | Unmatched |
|---|--:|--:|---|--:|
| **miguel_autentico** | 35.4 | — | 4-5-1 | 0 |
| Albert Laporta | 33.6 | -1.7 | 4-3-3 | 0 |
| Magic Mike 333 | 32.4 | -2.9 | 3-4-3 | 0 |
| BurtonGM89 | 31.4 | -3.9 | 5-4-1 | 0 |
| SusoGattuso | 29.4 | -6.0 | 4-4-2 | 0 |

Unmatched names are absent from that manager's total, so a big number there understates them. Variance in one jornada dwarfs these gaps; over ten it does not.

**miguel_autentico** — 4-5-1 · ≈35 pts
- POR: ionut radu 5.9
- DEF: carl starfelt 3.1 · igor zubeldia 3.0 · robin le normand 2.6 · omar el hilali 2.4
- MED: jon moncayola 3.9 · lucien agoume 3.2 · pepelu 3.0 · ruben garcia 2.9 · iñigo ruiz de galarreta 2.7
- DEL: iñigo vicente 2.7

**Albert Laporta** — 4-3-3 · ≈34 pts
- POR: matias dituro 5.6
- DEF: leandro cabrera 4.1 · diego javier llorente 2.8 · pedro bigas 2.3 · juan foyth 2.3
- MED: arda guler 3.9 · marc roca 2.6 · ilaix moriba 1.9
- DEL: ferran jutgla 3.7 · raul moro 2.4 · asier villalibre 2.1

**Magic Mike 333** — 3-4-3 · ≈32 pts
- POR: alfonso herrero 3.5
- DEF: kike salas 4.3 · jose gaya 2.5 · lucas noubi 2.2
- MED: brahim diaz 2.3 · gustavo puerta 2.3 · williot swedberg 2.2 · marc bernal 2.2
- DEL: raphinha 5.0 · gorka guruzeta 3.7 · pere milla 2.2

**BurtonGM89** — 5-4-1 · ≈31 pts
- POR: marko dmitrovic 5.7
- DEF: carlos romero 4.1 · justin de haas 2.8 · quilindschy hartman 2.0 · carlos puga 2.0 · giacomo quagliata 1.4
- MED: santi comesaña 4.4 · antonio blanco 3.9 · denis suarez 1.7 · tajon buchanan 1.3~
- DEL: karl etta eyong 2.0

**SusoGattuso** — 4-4-2 · ≈29 pts
- POR: wojciech szczesny 1.1~
- DEF: yuri berchiche 3.4 · cesar tarrega 3.2 · abdel abqar 2.6 · jon aramburu 2.1
- MED: alvaro garcia 3.8 · aimar oroz 2.8 · lorenzo amatucci 2.3 · izan merino 2.0
- DEL: giuliano simeone 3.7 · andres martin 2.4

`~` start probability under 50% — the model expects rotation there, so that is where their real XI will differ from this one.

### Ledger warnings

- Magic Mike 333: net spend exceeds the 100M budget by 7.22M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

---

Sections 2 and 3 are hypotheses until the sample grows: with 35 ledger rows across 5 managers, a median is one or two deals. Sections 1, 5 and 6 are usable today.

## Who to buy


The 21 players you pasted as today's slate — everyone you can actually bid on right now, unfiltered. What each one is worth to your XI, and what to bid, is in the slate table at the top of this report.

### defensa

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| santiago mouriño | Villarreal | def | 40.48M | -118K | 70% |
| pelayo fernandez | Rayo | def | 1.72M | 0K | 50% |
| clemens riedel | Espanyol | def | 4.60M | -58K | 50% |
| alex freeman | Villarreal | def | 2.45M | -32K | 30% |
| dani vivian | Athletic | def | 10.82M | -318K | 30% |

### mediocampista

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| mario martin | Getafe | med | 3.83M | 88K | 80% |
| pablo fornals | Betis | med | 57.93M | -376K | 80% |
| oriol rey | Levante | med | 734K | -8K | 50% |

### delantero

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| zakaria eddahchouri | Deportivo | del | 1.88M | -25K | 30% |

---

## Squad detail


| Manager | Players | Squad value | Spent | Raised | Cash |
|---|--:|--:|--:|--:|--:|
| **miguel_autentico** | 15 | 171.50M | 55.37M | 17.79M | 63.29M |
| Albert Laporta | 14 | 224.79M | 125.28M | 30.47M | ~5.19M |
| BurtonGM89 | 13 | 221.64M | 127.61M | 30.03M | ~2.42M |
| Magic Mike 333 | 19 | 240.66M | 121.23M | 14.01M | — |
| SusoGattuso | 16 | 176.11M | 62.44M | 0K | ~37.56M |

`~` is an estimate, not an observed balance — see the basis notes at the bottom. Cash is a ceiling on what anyone can bid tomorrow, which is the point of tracking it.

### You (miguel_autentico)
15 players · 171.50M total · 9 at 70%+ · cash 63.29M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| ionut radu | Celta | por | 39.25M | -464K | 90% |
| alvaro fernandez | Deportivo | por | 4.73M | -68K | 20% |
| simon eriksson | Racing | por | 2.92M | -11K | 50% |
| carl starfelt | Celta | def | 13.85M | 70K | 60% |
| robin le normand | Atlético | def | 9.98M | 140K | 60% |
| igor zubeldia | Real Sociedad | def | 9.94M | 78K | 80% |
| omar el hilali | Espanyol | def | 8.84M | 61K | 80% |
| ruben garcia | Osasuna | med | 13.75M | 173K | 60% |
| iñigo ruiz de galarreta | Athletic | med | 12.24M | 60K | 60% |
| dani lorenzo | Málaga | med | 9.69M | 33K | 90% |
| pepelu | Valencia | med | 7.47M | 99K | 70% |
| beñat turrientes | Real Sociedad | med | 7.06M | 78K | 70% |
| jon moncayola | Osasuna | med | 6.79M | 107K | 90% |
| lucien agoume | Sevilla | med | 5.82M | 28K | 80% |
| iñigo vicente | Racing | del | 19.17M | 75K | 90% |

### Albert Laporta
14 players · 224.79M total · 4 at 70%+ · cash ~5.19M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| matias dituro | Elche | por | 7.48M | 187K | 90% |
| leandro cabrera | Espanyol | def | 16.91M | 114K | 95% |
| diego javier llorente | Betis | def | 13.14M | 97K | 60% |
| juan foyth | Villarreal | def | 11.99M | 264K | 50% |
| pedro bigas | Elche | def | 5.17M | -124K | 50% |
| arda guler | Real Madrid | med | 51.98M | 697K | 60% |
| eduardo camavinga | Real Madrid | med | 10.33M | -151K | 30% |
| ilaix moriba | Celta | med | 10.17M | -77K | 50% |
| marc roca | Betis | med | 5.35M | 16K | 60% |
| abde ezzalzouli | Betis | del | 45.28M | -1.08M | 30% |
| ayoze perez | Villarreal | del | 17.04M | 226K | 50% |
| raul moro | Osasuna | del | 13.32M | 102K | 60% |
| ferran jutgla | Celta | del | 9.46M | 84K | 80% |
| asier villalibre | Racing | del | 7.17M | 117K | 70% |

### BurtonGM89
13 players · 221.64M total · 6 at 70%+ · cash ~2.42M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| marko dmitrovic | Espanyol | por | 37.43M | 14K | 95% |
| carlos romero | Villarreal | def | 43.69M | 460K | 80% |
| eder militao | Real Madrid | def | 12.04M | -357K | 0% |
| justin de haas | Valencia | def | 9.11M | 118K | 70% |
| quilindschy hartman | Espanyol | def | 8.89M | -164K | 50% |
| carlos puga | Málaga | def | 5.28M | -40K | 70% |
| giacomo quagliata | Deportivo | def | 3.27M | -16K | 50% |
| santi comesaña | Villarreal | med | 34.21M | 535K | 90% |
| tajon buchanan | Villarreal | med | 18.37M | -119K | 30% |
| antonio blanco | Alavés | med | 14.95M | -7K | 90% |
| jon gorrotxategi | Real Sociedad | med | 6.38M | -158K | 30% |
| denis suarez | Alavés | med | 2.51M | -31K | 50% |
| karl etta eyong | Levante | del | 25.50M | 20K | 50% |

### Magic Mike 333
19 players · 240.66M total · 6 at 70%+ · cash —

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| alfonso herrero | Málaga | por | 8.20M | 103K | 80% |
| lucas noubi | Deportivo | def | 12.29M | 248K | 80% |
| kike salas | Sevilla | def | 12.06M | 121K | 90% |
| jose gaya | Valencia | def | 10.98M | -72K | 60% |
| raul asencio | Real Madrid | def | 4.57M | -138K | 0% |
| fabio cardoso | Sevilla | def | 842K | -18K | 0% |
| alex pastor | Málaga | def | 376K | -3K | 40% |
| pablo gavi | Barcelona | med | 32.69M | -707K | 50% |
| gustavo puerta | Racing | med | 12.64M | 88K | 80% |
| brahim diaz | Real Madrid | med | 10.59M | 65K | 50% |
| gabriel moscardo | Espanyol | med | 9.45M | -201K | 30% |
| williot swedberg | Celta | med | 8.06M | -8K | 50% |
| marc bernal | Barcelona | med | 6.90M | 245K | 60% |
| pedro diaz | Rayo | med | 1.78M | -21K | 50% |
| raphinha | Barcelona | del | 83.51M | 1.53M | 70% |
| gorka guruzeta | Athletic | del | 13.61M | 78K | 80% |
| pere milla | Espanyol | del | 9.58M | -11K | 50% |
| tete morente | Elche | del | 1.94M | -42K | 40% |
| jon karrikaburu | Real Sociedad | del | 570K | -11K | 0% |

### SusoGattuso
16 players · 176.11M total · 10 at 70%+ · cash ~37.56M

| Player | Team | Pos | Value | 24h | Start% |
|---|---|--:|--:|--:|--:|
| wojciech szczesny | Barcelona | por | 2.35M | -46K | 20% |
| jon aramburu | Real Sociedad | def | 17.30M | 127K | 80% |
| yuri berchiche | Athletic | def | 12.51M | -42K | 70% |
| cesar tarrega | Valencia | def | 9.00M | 59K | 80% |
| abdel abqar | Getafe | def | 7.05M | 124K | 70% |
| jonny castro | Alavés | def | 5.44M | -27K | 50% |
| alvaro garcia | Villarreal | def | 502K | 0K | 80% |
| aleksa puric | Atlético | def | 435K | 0K | — |
| aimar oroz | Osasuna | med | 15.80M | 42K | 70% |
| lorenzo amatucci | Deportivo | med | 12.84M | 290K | 80% |
| izan merino | Málaga | med | 6.40M | -47K | 70% |
| johnny cardoso | Atlético | med | 5.81M | -113K | 30% |
| giuliano simeone | Atlético | del | 43.85M | -308K | 70% |
| andres martin | Racing | del | 19.12M | -135K | 80% |
| alex berenguer | Athletic | del | 8.97M | -62K | 30% |
| carlos espi | Real Madrid | del | 8.74M | 138K | 30% |

### What they pay

| Date | Player | From → To | Price |
|---|---|---|--:|
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
| 2026-08-14T12:50 | lucas boye | Magic Mike 333 → market | 14013107 |
| 2026-08-14T21:24 | ionut radu | market → miguel_autentico | 39655832 |
| 2026-08-14T21:24 | agoume | market → miguel_autentico | 5891526 |
| 2026-08-14T21:24 | eriksson | market → miguel_autentico | 2933863 |
| 2026-08-14T21:24 | gavi | market → Magic Mike 333 | 35500000 |
| 2026-08-14T21:24 | t. morente | market → Magic Mike 333 | 2033651 |
| 2026-08-14T21:24 | aramburu | market → SusoGattuso | 17785551 |

### Cash basis

- **miguel_autentico** — balance you recorded on 2026-08-15, then 0 ledger row(s) (known)
- **Albert Laporta** — 100M starting budget, then 8 ledger row(s) (estimated)
- **BurtonGM89** — 100M starting budget, then 11 ledger row(s) (estimated)
- **Magic Mike 333** — 100M starting budget, then 7 ledger row(s), which overdraws it by 7.22M (unknown)
- **SusoGattuso** — 100M starting budget, then 2 ledger row(s) (estimated)

### Names the ledger did not spell exactly

Placed by who the counterparty was, or by what the price implies — a player sold by a manager was in that manager's squad, and a player bought from the market was in nobody's (issue #26). Fix the spelling in `inputs/transactions.csv` if one of these is the wrong player.

- 2026-08-14T21:24: agoume → lucien agoume (matched lucien agoume)
- 2026-08-14T21:24: eriksson → simon eriksson (matched simon eriksson)
- 2026-08-14T21:24: gavi → pablo gavi (matched pablo gavi)
- 2026-08-14T21:24: t. morente → tete morente (matched tete morente)
- 2026-08-14T21:24: aramburu → jon aramburu (matched jon aramburu)

## How the forecast works — and how it's doing


### The formula

Every player's **xPts/j** — expected points per jornada — is:

    xPts/j = shrunk points-per-match × P(start)

**Shrunk points-per-match** pulls a player's last-season average toward the median for his position: `(points + 8×prior) / (matches + 8)`, prior = median pts/match among players in that position with 10+ matches. 8 matches of prior weight means a 3-game wonder is mostly prior and a 34-game regular is mostly himself.

**P(start)** is futbolfantasy's probable-XI percentage, read twice daily. A player listed without a percentage gets a neutral prior; one absent from the page entirely gets a low one. Promoted-side players have no top-flight record, fall back to the positional prior, and are marked **assumed**.

The **team forecast** is the sum over the best legal XI, so ≈35 means: this eleven is expected to score about 35 points in a jornada, before variance — and single-match variance is huge.

### What it deliberately ignores (for now)

- **Fixtures** — no opponent-strength or home/away adjustment.
- **Sub cameos** — P(start) multiplies the whole average, so a 30% starter is modelled as 0.3 × his points, when in reality he often plays 20 minutes and scores something. Forecasts for rotation players run low.
- **This season** — the baseline is last season until the live points blend is turned on deliberately; a two-jornada sample should not drive an XI.

Each of these is a candidate fix, but only after the comparison below shows which one actually costs points.

### Forecast vs actual — last 21 days

_No completed jornada in the window yet. points.py has no per-jornada rows to compare against; this section fills itself in after the first matches._
