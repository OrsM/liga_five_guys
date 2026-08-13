# League behaviour — 2026-08-13 21:45 UTC

5 managers, 18 ledger rows, 23 market snapshots, points baseline 2025-26.

## 1. Cash and ceilings

| Manager | Players | Spent | Raised | Net | Cash | Max bid |
|---|--:|--:|--:|--:|--:|--:|
| **miguel_autentico** | 12 | 6.89M | 17.79M | 10.90M | 111.37M | 111.37M |
| Albert Laporta | 11 | 49.99M | 30.47M | -19.52M | ~80.48M | 80.48M |
| BurtonGM89 | 17 | 125.03M | 469K | -124.56M | — | — |
| Magic Mike 333 | 18 | 83.70M | 0K | -83.70M | ~16.30M | 16.30M |
| SusoGattuso | 14 | 0K | 0K | 0K | ~100.00M | 100.00M |

`~` is an estimate: the starting budget less every ledger row, not an observed balance. The starting squad was dealt free, so it costs nothing here. A `—` means the ledger overdraws the budget, so the number would be fiction — see the warnings. Any time a rival mentions a balance, put it in `inputs/cash.txt` — one observed number turns their whole estimate into arithmetic.

## 2. What they pay over value

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

## 3. What happened next

_No horizon has elapsed inside the snapshot history yet. Needs 3 days of daily ingest past a transaction._

## 4. Squad diagnostics

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

## 5. Who wants what

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

## Ledger warnings

- BurtonGM89: net spend exceeds the 100M budget by 24.56M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

---

Sections 2 and 3 are hypotheses until the sample grows: with 18 ledger rows across 5 managers, a median is one or two deals. Section 1 and section 5 are usable today.
