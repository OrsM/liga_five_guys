# League behaviour — 2026-08-15 08:26 UTC

5 managers, 35 ledger rows, 29 market snapshots, points baseline 2025-26.

## 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 15 | 55.37M | 17.79M | -37.58M | 63.29M | 63.29M |
| Albert Laporta | 14 | 125.28M | 30.47M | -94.81M | ~5.19M | 5.19M |
| BurtonGM89 | 13 | 127.61M | 30.03M | -97.58M | ~2.42M | 2.42M |
| Magic Mike 333 | 19 | 121.23M | 14.01M | -107.22M | — | — |
| SusoGattuso | 16 | 62.44M | 0K | -62.44M | ~37.56M | 37.56M |

`~` is an estimate: the starting budget less every ledger row, not an observed balance. The starting squad was dealt free, so it costs nothing here. A `—` means the ledger overdraws the budget, so the number would be fiction — see the warnings. Any time a rival mentions a balance, put it in `inputs/cash.txt` — one observed number turns their whole estimate into arithmetic.

**Cash-constrained right now:** BurtonGM89 (2.42M). Against these, open at the minimum increment — they cannot escalate.

## 2. What they pay over value

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

## 3. What happened next

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

## 4. Squad diagnostics

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

## 5. Who wants what

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

## 6. Projected XIs

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

## Ledger warnings

- Magic Mike 333: net spend exceeds the 100M budget by 7.22M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

---

Sections 2 and 3 are hypotheses until the sample grows: with 35 ledger rows across 5 managers, a median is one or two deals. Sections 1, 5 and 6 are usable today.
