# League behaviour — 2026-08-15 22:55 UTC

5 managers, 41 ledger rows, 32 market snapshots, points 2025-26.

## 1. Cash and ceilings

| Manager | Players | Base | Bought | Sold | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 16 | 63.29M | 0K | 0K | 63.29M | 63.29M |
| Albert Laporta | 15 | 100.00M | 173.13M | 35.80M | ~-37.33M | 0K |
| BurtonGM89 | 13 | 100.00M | 127.61M | 30.03M | ~2.42M | 2.42M |
| Magic Mike 333 | 19 | 100.00M | 121.23M | 14.01M | ~-7.22M | 0K |
| SusoGattuso | 18 | 100.00M | 70.87M | 0K | ~29.13M | 29.13M |

**Base − Bought + Sold = Cash**, row by row. Base is the last balance you recorded for them in `inputs/cash.txt`, or the starting budget when there is none, and Bought/Sold count only the ledger rows since — so the row adds up either way. `~` marks a balance derived rather than observed. The starting squad was dealt free, so it costs nothing here. Any time a rival mentions a balance, write it down: one observed number turns their whole estimate into arithmetic.

**Overdrawn, which is allowed until the lock.** Committing past the balance mid-window is legal; being under water when the jornada locks is not. Each of these has to sell before buying again — or the ledger is missing a sale of theirs, in which case the figure is stale rather than wrong.

- **Albert Laporta** — ~-37.33M. 100M starting budget − 173.13M bought + 35.80M sold across 11 ledger row(s) = -37.33M
- **Magic Mike 333** — ~-7.22M. 100M starting budget − 121.23M bought + 14.01M sold across 7 ledger row(s) = -7.22M

**Cash-constrained right now:** Albert Laporta (0K), BurtonGM89 (2.42M), Magic Mike 333 (0K). Against these, open at the minimum increment — they cannot escalate today without selling first.

## 2. What they pay over value

| Manager | Buys | Median premium | Range | Round bids |
|---|--:|--:|---|--:|
| miguel_autentico | 5 | +0.0% | -0.1% to +1.7% | 0/5 |
| Albert Laporta | 5 | +0.0% | -0.2% to +5.0% | 0/6 |
| BurtonGM89 | 5 | +9.2% | +0.0% to +21.6% | 2/5 |
| Magic Mike 333 | 6 | +6.3% | +2.4% to +15.9% | 4/6 |
| SusoGattuso | 4 | +2.4% | -1.2% to +3.6% | 0/4 |

**The floor sometimes wins.** 9 of the 25 priced purchases in this league went at the market value itself and the other 16 cleared it, median +2.4%, -1.2% to +21.6% (n=25) across all of them. Bidding the minimum is therefore not the one number known to lose — but 9 of 25 is a share of the bids that WON, not the odds of winning one. Nothing in this ledger records a bid that lost, so the floor's failure rate is unmeasured and unmeasurable from here.

**The app does not pay you the value — it randomises around it.** The 14 priced sales back to the market went for median +2.4%, -9.4% to +12.0% (n=14): 6 below the value and 8 above, never further than 12.0% either way. So a sale raises the value give or take a tenth, and the value is not the money you will get. Whether the same randomiser bids against you for a free agent is inferred, not measured: every row in this ledger is a bid that won.

A round bid was typed by a human. That is the whole of what roundness tells you — an exact bid is *not* the app's valuation and does not mean nobody competed, because the premium column two cells left already measures how far above the floor the buyer went. Sealed bids are paid as bid, so a purchase at exactly the value was only ever yours to take if the tie-break favoured you, and that rule is not documented anywhere we can read. Check it in-app before reading a floor purchase as a bargain you missed.

| Date | Player | Buyer | Paid | Value then | Premium | Bid |
|---|---|---|--:|--:|--:|---|
| 08-14T21:24 | ionut radu | miguel_autentico | 39.66M | 39.71M | -0.1% | exact |
| 08-14T21:24 | lucien agoume | miguel_autentico | 5.89M | 5.79M | +1.7% | exact |
| 08-14T21:24 | simon eriksson | miguel_autentico | 2.93M | 2.93M | +0.0% | exact |
| 08-14T21:24 | pablo gavi | Magic Mike 333 | 35.50M | 33.40M | +6.3% | round |
| 08-14T21:24 | tete morente | Magic Mike 333 | 2.03M | 1.99M | +2.4% | exact |
| 08-14T21:24 | jon aramburu | SusoGattuso | 17.79M | 17.18M | +3.6% | exact |
| 08-14T21:24 | facundo buonanotte | Albert Laporta | 7.33M | — ~ | — | exact |
| 08-14T21:24 | clemens riedel | SusoGattuso | 4.60M | 4.66M | -1.2% | exact |
| 08-14T21:24 | mario martin | SusoGattuso | 3.83M | 3.74M | +2.4% | exact |
| 08-14T21:24 | pablo fornals | miguel_autentico | 58.22M | 58.30M | -0.1% | exact |
| 08-14T21:24 | santiago mouriño | Albert Laporta | 40.52M | 40.60M | -0.2% | exact |
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

`~` priced against a snapshot more than 36h away and left out of the medians.

## 3. What happened next

| Date | Player | Actor | Side | +3d | +7d | +14d |
|---|---|---|---|--:|--:|--:|
| 08-12T21:24 | abde ezzalzouli | Albert Laporta | buy | -8.8% | — | — |
| 08-12T21:24 | santi comesaña | BurtonGM89 | buy | +7.3% | — | — |
| 08-12T21:24 | jon karrikaburu | Magic Mike 333 | buy | -6.9% | — | — |
| 08-12T21:24 | pedro diaz | Magic Mike 333 | buy | -5.5% | — | — |
| 08-12T21:24 | raphinha | Magic Mike 333 | buy | +10.6% | — | — |
| 08-12T21:24 | marko dmitrovic | BurtonGM89 | buy | +2.0% | — | — |
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
| **miguel_autentico** | 39.0 | 4-5-1 | 4.65M | 0 | del | 0 |
| Albert Laporta | 35.0 | 4-3-3 | 54.60M | 0 | por | 1 |
| BurtonGM89 | 34.9 | 5-4-1 | 36.22M | 1 | por,del | 0 |
| Magic Mike 333 | 33.7 | 3-4-3 | 19.12M | 1 | por | 0 |
| SusoGattuso | 36.1 | 4-4-2 | 26.27M | 0 | por | 0 |

- miguel_autentico is carrying more portero/mediocampista than can ever start.
- Albert Laporta is carrying more delantero than can ever start.
- BurtonGM89 is carrying more defensa than can ever start.
- Magic Mike 333 is carrying more defensa/mediocampista/delantero than can ever start.
- SusoGattuso is carrying more mediocampista/delantero/defensa than can ever start.

Trapped is value held in players below 50% start probability — money that cannot score. Unmatched is names in their squad missing from data/tidy, which are absent from the xPts total, so a large number there means the comparison flatters you.

## 5. Who wants what

**Expect competition for these** — the position is one a rival is short in, so assume a bidding war and price accordingly.

| Player | Pos | Value | FF | AF | Short here |
|---|---|--:|--:|--:|---|
| david soria | POR | 19.98M | 100% | titular | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| augusto batalla | POR | 40.07M | 100% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| joan garcia | POR | 68.69M | 80% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| jan oblak | POR | 52.33M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| lamine yamal | DEL | 126.12M | 60% | — | BurtonGM89 |
| kylian mbappe | DEL | 128.76M | 70% | — | BurtonGM89 |
| vinicius junior | DEL | 107.58M | 90% | — | BurtonGM89 |
| martin satriano | DEL | 31.07M | 100% | — | BurtonGM89 |
| alvaro valles | POR | 33.11M | 95% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| jorge de frutos | DEL | 45.88M | 100% | titular | BurtonGM89 |
| unai simon | POR | 54.03M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| mathew ryan | POR | 13.55M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

**Nobody else needs these.** Same quality, no auction — take the equivalent player here instead of paying a premium above.

| Player | Pos | Value | FF | AF |
|---|---|--:|--:|--:|
| zaid romero | DEF | 28.06M | 100% | titular |
| florian lejeune | DEF | 38.32M | 100% | — |
| federico valverde | MED | 69.03M | 90% | — |
| andrei ratiu | DEF | 35.60M | 100% | titular |
| unai lopez | MED | 5.91M | 100% | titular |
| pathe ciss | MED | 12.03M | 100% | titular |
| marcos alonso | DEF | 29.19M | 90% | 67% |
| fermin lopez | MED | 65.27M | 70% | — |

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | FF | AF | Short |
|---|---|--:|--:|--:|---|
| alvaro fernandez | POR | 4.65M | 20% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

**FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both.

## 6. Projected XIs

Each manager's best legal XI under the same scorer — what a rational version of them fields. Once jornadas run, their actual points versus this forecast measures two things at once: the model's calibration (5× the sample your own squad gives), and who manages actively versus who set-and-forgets — a leak worth knowing at deal time.

| Manager | ≈pts/j | vs you | Shape | Unmatched |
|---|--:|--:|---|--:|
| **miguel_autentico** | 39.0 | — | 4-5-1 | 0 |
| SusoGattuso | 36.1 | -2.9 | 4-4-2 | 0 |
| Albert Laporta | 35.0 | -4.0 | 4-3-3 | 1 |
| BurtonGM89 | 34.9 | -4.1 | 5-4-1 | 0 |
| Magic Mike 333 | 33.7 | -5.3 | 3-4-3 | 0 |

Unmatched names are absent from that manager's total, so a big number there understates them. Variance in one jornada dwarfs these gaps; over ten it does not.

**miguel_autentico** — 4-5-1 · ≈39 pts
- POR: ionut radu 6.0
- DEF: carl starfelt 3.1 · robin le normand 2.8 · omar el hilali 2.8 · igor zubeldia 2.0
- MED: pablo fornals 5.4 · jon moncayola 4.5 · lucien agoume 3.6 · ruben garcia 3.3 · pepelu 3.2
- DEL: iñigo vicente 2.6

**SusoGattuso** — 4-4-2 · ≈36 pts
- POR: wojciech szczesny 1.2~
- DEF: abdel abqar 4.3 · yuri berchiche 3.8 · cesar tarrega 3.4 · jonny castro 2.8
- MED: alvaro garcia 5.4 · mario martin 3.3 · aimar oroz 3.1 · lorenzo amatucci 2.6
- DEL: giuliano simeone 3.9 · andres martin 2.3

**Albert Laporta** — 4-3-3 · ≈35 pts
- POR: matias dituro 5.3
- DEF: leandro cabrera 4.7 · santiago mouriño 3.3 · juan foyth 2.4 · diego javier llorente 2.4
- MED: arda guler 4.0 · marc roca 2.6 · ilaix moriba 1.9
- DEL: ferran jutgla 3.7 · raul moro 2.7 · ayoze perez 2.2

**BurtonGM89** — 5-4-1 · ≈35 pts
- POR: marko dmitrovic 6.5
- DEF: carlos romero 4.4 · justin de haas 3.0 · quilindschy hartman 2.3 · carlos puga 1.7 · giacomo quagliata 1.6
- MED: santi comesaña 4.7 · antonio blanco 4.0 · denis suarez 3.2 · tajon buchanan 1.4~
- DEL: karl etta eyong 2.1

**Magic Mike 333** — 3-4-3 · ≈34 pts
- POR: alfonso herrero 3.0
- DEF: kike salas 4.3 · jose gaya 2.7 · lucas noubi 2.6
- MED: marc bernal 2.4 · brahim diaz 2.3 · williot swedberg 2.2 · gustavo puerta 2.2
- DEL: raphinha 5.3 · gorka guruzeta 4.1 · pere milla 2.5

`~` start probability under 50% — the model expects rotation there, so that is where their real XI will differ from this one.

## How much of this to believe

Sections 2 and 3 are hypotheses until the sample grows: with 41 ledger rows across 5 managers, a median is one or two deals. Sections 1, 5 and 6 are usable today.

## Ledger warnings

- Magic Mike 333 is 7.22M overdrawn: 100M starting budget − 121.23M bought + 14.01M sold across 7 ledger row(s) = -7.22M. Going over the budget mid-window is allowed; being overdrawn when the jornada locks is not, so they must sell before they can buy again. If the ledger is missing a sale of theirs, this is stale rather than wrong.
- Albert Laporta is 37.33M overdrawn: 100M starting budget − 173.13M bought + 35.80M sold across 11 ledger row(s) = -37.33M. Going over the budget mid-window is allowed; being overdrawn when the jornada locks is not, so they must sell before they can buy again. If the ledger is missing a sale of theirs, this is stale rather than wrong.
