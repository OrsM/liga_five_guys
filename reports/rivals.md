# League behaviour — 2026-08-17 10:17 UTC

5 managers, 44 ledger rows, 36 market snapshots, points 2025-26.

## 1. Cash and ceilings

| Manager | Players | Base | Bought | Sold | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 16 | 63.29M | 0K | 0K | 63.29M | 63.29M |
| Albert Laporta | 15 | 100.00M | 173.13M | 35.80M | ~-37.33M | 0K |
| BurtonGM89 | 14 | 100.00M | 165.86M | 42.04M | ~-23.82M | 0K |
| Magic Mike 333 | 19 | 100.00M | 121.23M | 14.01M | ~-7.22M | 0K |
| SusoGattuso | 18 | 100.00M | 70.87M | 0K | ~29.13M | 29.13M |

**Base − Bought + Sold = Cash**, row by row. Base is the last balance you recorded for them in `inputs/cash.txt`, or the starting budget when there is none, and Bought/Sold count only the ledger rows since — so the row adds up either way. `~` marks a balance derived rather than observed. The starting squad was dealt free, so it costs nothing here. Any time a rival mentions a balance, write it down: one observed number turns their whole estimate into arithmetic.

**Overdrawn, which is allowed until the lock.** Committing past the balance mid-window is legal; being under water when the jornada locks is not. Each of these has to sell before buying again — or the ledger is missing a sale of theirs, in which case the figure is stale rather than wrong.

- **Albert Laporta** — ~-37.33M. 100M starting budget − 173.13M bought + 35.80M sold across 11 ledger row(s) = -37.33M
- **BurtonGM89** — ~-23.82M. 100M starting budget − 165.86M bought + 42.04M sold across 14 ledger row(s) = -23.82M
- **Magic Mike 333** — ~-7.22M. 100M starting budget − 121.23M bought + 14.01M sold across 7 ledger row(s) = -7.22M

**Cash-constrained right now:** Albert Laporta (0K), BurtonGM89 (0K), Magic Mike 333 (0K). Against these, open at the minimum increment — they cannot escalate today without selling first.

## 2. What they pay over value

| Manager | Buys | Median premium | Range | Round bids |
|---|--:|--:|---|--:|
| miguel_autentico | 5 | +0.0% | -0.1% to +1.7% | 0/5 |
| Albert Laporta | 5 | +0.0% | -0.2% to +5.0% | 0/6 |
| BurtonGM89 | 7 | +8.6% | +0.0% to +21.6% | 3/7 |
| Magic Mike 333 | 6 | +6.3% | +2.4% to +15.9% | 4/6 |
| SusoGattuso | 4 | +2.4% | -1.2% to +3.6% | 0/4 |

**The floor sometimes wins.** 9 of the 27 priced purchases in this league went at the market value itself and the other 18 cleared it, median +2.6%, -1.2% to +21.6% (n=27) across all of them. Bidding the minimum is therefore not the one number known to lose — but 9 of 27 is a share of the bids that WON, not the odds of winning one. Nothing in this ledger records a bid that lost, so the floor's failure rate is unmeasured and unmeasurable from here.

**The app does not pay you the value — it randomises around it.** The 15 priced sales back to the market went for median +1.5%, -9.4% to +12.0% (n=15): 7 below the value and 8 above, never further than 12.0% either way. So a sale raises the value give or take a tenth, and the value is not the money you will get. Whether the same randomiser bids against you for a free agent is inferred, not measured: every row in this ledger is a bid that won.

A round bid was typed by a human. That is the whole of what roundness tells you — an exact bid is *not* the app's valuation and does not mean nobody competed, because the premium column two cells left already measures how far above the floor the buyer went. Sealed bids are paid as bid, so a purchase at exactly the value was only ever yours to take if the tie-break favoured you, and that rule is not documented anywhere we can read. Check it in-app before reading a floor purchase as a bargain you missed.

| Date | Player | Buyer | Paid | Value then | Premium | Bid |
|---|---|---|--:|--:|--:|---|
| 08-14T21:24 | ionut radu | miguel_autentico | 39.66M | 39.71M | -0.1% | exact |
| 08-14T21:24 | lucien agoume | miguel_autentico | 5.89M | 5.79M | +1.7% | exact |
| 08-14T21:24 | simon eriksson | miguel_autentico | 2.93M | 2.93M | +0.0% | exact |
| 08-14T21:24 | pablo gavi | Magic Mike 333 | 35.50M | 33.40M | +6.3% | round |
| 08-14T21:24 | tete morente | Magic Mike 333 | 2.03M | 1.99M | +2.4% | exact |
| 08-14T21:24 | jon aramburu | SusoGattuso | 17.79M | 17.18M | +3.6% | exact |
| 08-14T21:24 | facundo buonanotte | Albert Laporta | 7.33M | 6.83M ~ | +7.3% | exact |
| 08-14T21:24 | clemens riedel | SusoGattuso | 4.60M | 4.66M | -1.2% | exact |
| 08-14T21:24 | mario martin | SusoGattuso | 3.83M | 3.74M | +2.4% | exact |
| 08-14T21:24 | pablo fornals | miguel_autentico | 58.22M | 58.30M | -0.1% | exact |
| 08-14T21:24 | santiago mouriño | Albert Laporta | 40.52M | 40.60M | -0.2% | exact |
| 08-14T21:24 | huijsen | BurtonGM89 | 32.90M | 31.45M | +4.6% | exact |
| 08-14T21:24 | paredes | BurtonGM89 | 5.35M | 4.96M | +7.8% | round |
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

`~` priced against a snapshot more than 36h away and left out of the medians.

## 3. What happened next

| Date | Player | Actor | Side | +3d | +7d | +14d |
|---|---|---|---|--:|--:|--:|
| 08-13T21:26 | iago aspas | BurtonGM89 | sell | -3.6% | — | — |
| 08-13T21:26 | unai lopez | BurtonGM89 | sell | +2.5% | — | — |
| 08-13T21:26 | joaquin muñoz | BurtonGM89 | sell | -5.8% | — | — |
| 08-13T21:26 | aliou dieng | BurtonGM89 | sell | -8.8% | — | — |
| 08-13T21:25 | juan musso | BurtonGM89 | sell | -7.3% | — | — |
| 08-13T21:24 | leandro cabrera | Albert Laporta | buy | +2.1% | — | — |
| 08-13T21:24 | arda guler | Albert Laporta | buy | +5.5% | — | — |
| 08-13T21:24 | giuliano simeone | SusoGattuso | buy | -3.4% | — | — |
| 08-13T21:24 | asier villalibre | Albert Laporta | buy | +4.8% | — | — |
| 08-13T21:24 | denis suarez | BurtonGM89 | buy | -4.7% | — | — |
| 08-13T12:13 | hugo duro | miguel_autentico | sell | -0.0% | — | — |
| 08-13T12:12 | orri steinn oskarsson | miguel_autentico | sell | -4.8% | — | — |
| 08-13T12:12 | dani martinez | miguel_autentico | sell | +0.8% | — | — |
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

Two errors this table is built to catch: buying a player who has already risen (paying the top of the move), and selling one who has just dipped (realising the bottom). Both show as the drift column reversing sign against the actor.

## 4. Squad diagnostics

| Manager | xPts/j | Shape | Trapped | Injured | Thin at | Unmatched |
|---|--:|---|--:|--:|---|--:|
| **miguel_autentico** | 38.6 | 4-5-1 | 7.44M | 0 | del | 0 |
| Albert Laporta | 31.4 | 3-4-3 | 77.52M | 0 | por | 0 |
| BurtonGM89 | 31.9 | 5-4-1 | 32.66M | 0 | por,del | 0 |
| Magic Mike 333 | 29.8 | 3-5-2 | 40.14M | 1 | por | 0 |
| SusoGattuso | 32.0 | 5-3-2 | 26.20M | 0 | por | 0 |

- miguel_autentico is carrying more portero/mediocampista than can ever start.
- Albert Laporta is carrying more delantero than can ever start.
- BurtonGM89 is carrying more defensa than can ever start.
- Magic Mike 333 is carrying more defensa/mediocampista/delantero than can ever start.
- SusoGattuso is carrying more defensa/delantero than can ever start.

Trapped is value held in players below 50% start probability — money that cannot score. Unmatched is names in their squad missing from data/tidy, which are absent from the xPts total, so a large number there means the comparison flatters you.

## 5. Who wants what

**Expect competition for these** — the position is one a rival is short in, so assume a bidding war and price accordingly.

| Player | Pos | Value | FF | AF | Short here |
|---|---|--:|--:|--:|---|
| david soria | POR | 20.46M | 95% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| joan garcia | POR | 69.44M | 80% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| augusto batalla | POR | 39.61M | 95% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| jan oblak | POR | 51.92M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| kylian mbappe | DEL | 128.45M | 70% | 100% | BurtonGM89 |
| vinicius junior | DEL | 107.84M | 90% | — | BurtonGM89 |
| lamine yamal | DEL | 126.03M | 60% | 100% | BurtonGM89 |
| alvaro valles | POR | 33.42M | 95% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| unai simon | POR | 53.41M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| stole dimitrievski | POR | 14.68M | 90% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| ante budimir | DEL | 51.49M | 90% | — | BurtonGM89 |
| mathew ryan | POR | 13.54M | 90% | 100% | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

**Nobody else needs these.** Same quality, no auction — take the equivalent player here instead of paying a premium above.

| Player | Pos | Value | FF | AF |
|---|---|--:|--:|--:|
| zaid romero | DEF | 27.95M | 90% | 100% |
| florian lejeune | DEF | 38.07M | 90% | — |
| federico valverde | MED | 69.63M | 90% | — |
| andrei ratiu | DEF | 35.68M | 90% | 100% |
| marcos alonso | DEF | 28.93M | 90% | 67% |
| guido rodriguez | MED | 25.24M | 90% | 100% |
| fermin lopez | MED | 66.84M | 70% | — |
| alejandro catena | DEF | 33.67M | 80% | — |

**List these to them.** Players of yours who aren't starting, in a position a rival is short in. You stop competing with them and start selling to them; price just under the premium they showed in section 2.

| Player | Pos | Value | FF | AF | Short |
|---|---|--:|--:|--:|---|
| alvaro fernandez | POR | 4.58M | 20% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |
| simon eriksson | POR | 2.86M | 30% | — | Albert Laporta, BurtonGM89, Magic Mike 333, SusoGattuso |

**FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both.

## 6. Projected XIs

Each manager's best legal XI under the same scorer — what a rational version of them fields. Once jornadas run, their actual points versus this forecast measures two things at once: the model's calibration (5× the sample your own squad gives), and who manages actively versus who set-and-forgets — a leak worth knowing at deal time.

| Manager | ≈pts/j | vs you | Shape | Unmatched |
|---|--:|--:|---|--:|
| **miguel_autentico** | 38.6 | — | 4-5-1 | 0 |
| SusoGattuso | 32.0 | -6.6 | 5-3-2 | 0 |
| BurtonGM89 | 31.9 | -6.7 | 5-4-1 | 0 |
| Albert Laporta | 31.4 | -7.2 | 3-4-3 | 0 |
| Magic Mike 333 | 29.8 | -8.8 | 3-5-2 | 0 |

Unmatched names are absent from that manager's total, so a big number there understates them. Variance in one jornada dwarfs these gaps; over ten it does not.

**miguel_autentico** — 4-5-1 · ≈39 pts
- POR: ionut radu 6.0
- DEF: carl starfelt 3.1 · robin le normand 2.8 · omar el hilali 2.2 · igor zubeldia 2.0
- MED: pablo fornals 5.4 · jon moncayola 4.6 · iñigo ruiz de galarreta 3.5 · ruben garcia 3.4 · pepelu 3.2
- DEL: iñigo vicente 2.6

**SusoGattuso** — 5-3-2 · ≈32 pts
- POR: wojciech szczesny 1.2~
- DEF: yuri berchiche 3.8 · cesar tarrega 3.4 · clemens riedel 3.2 · abdel abqar 3.0 · alvaro garcia 2.9
- MED: aimar oroz 3.2 · lorenzo amatucci 2.6 · mario martin 2.3
- DEL: giuliano simeone 3.9 · andres martin 2.6

**BurtonGM89** — 5-4-1 · ≈32 pts
- POR: marko dmitrovic 5.3
- DEF: dean huijsen 4.0 · aitor paredes 3.5 · carlos romero 3.2 · justin de haas 3.0 · carlos puga 1.7
- MED: antonio blanco 3.7 · santi comesaña 2.6 · denis suarez 1.6 · tajon buchanan 1.5~
- DEL: karl etta eyong 2.0

**Albert Laporta** — 3-4-3 · ≈31 pts
- POR: matias dituro 5.2
- DEF: juan foyth 2.8 · santiago mouriño 2.7 · diego javier llorente 2.4
- MED: arda guler 4.0 · marc roca 2.6 · ilaix moriba 1.9 · facundo buonanotte 1.5~
- DEL: ferran jutgla 3.7 · raul moro 2.7 · asier villalibre 2.0

**Magic Mike 333** — 3-5-2 · ≈30 pts
- POR: alfonso herrero 3.0
- DEF: jose gaya 2.7 · lucas noubi 2.6 · alex pastor 1.0~
- MED: gustavo puerta 2.4 · marc bernal 2.3 · brahim diaz 2.3 · williot swedberg 2.2 · pablo gavi 1.9
- DEL: raphinha 5.2 · gorka guruzeta 4.1

`~` start probability under 50% — the model expects rotation there, so that is where their real XI will differ from this one.

## How much of this to believe

Sections 2 and 3 are hypotheses until the sample grows: with 44 ledger rows across 5 managers, a median is one or two deals. Sections 1, 5 and 6 are usable today.

## Ledger warnings

- Magic Mike 333 is 7.22M overdrawn: 100M starting budget − 121.23M bought + 14.01M sold across 7 ledger row(s) = -7.22M. Going over the budget mid-window is allowed; being overdrawn when the jornada locks is not, so they must sell before they can buy again. If the ledger is missing a sale of theirs, this is stale rather than wrong.
- Albert Laporta is 37.33M overdrawn: 100M starting budget − 173.13M bought + 35.80M sold across 11 ledger row(s) = -37.33M. Going over the budget mid-window is allowed; being overdrawn when the jornada locks is not, so they must sell before they can buy again. If the ledger is missing a sale of theirs, this is stale rather than wrong.
- BurtonGM89 is 23.82M overdrawn: 100M starting budget − 165.86M bought + 42.04M sold across 14 ledger row(s) = -23.82M. Going over the budget mid-window is allowed; being overdrawn when the jornada locks is not, so they must sell before they can buy again. If the ledger is missing a sale of theirs, this is stale rather than wrong.
