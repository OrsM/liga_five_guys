# The simulation — 2026-08-18T0941Z

## What the simulation says to do

_One question, asked of every move you could make: if I did this, where would I finish?_

**Locks in 30h** · squad 213.11M · cash 23.60M · total 236.71M

**Expected finish 1.61** · **P(win) 49%** · season **1,311–1,565** (10–90)

_38 jornadas left · 83 players acquirable · 132 moves simulated._

| Do this | Δpos | Δwin | net € | biggest gain vs |
|---|--:|--:|--:|---|
| steal Yuri Berchiche from SusoGattuso · sell Beñat Turrientes | +0.433 | +36% | -14.13M | SusoGattuso +36% |
| buy Marcos Alonso · sell Beñat Turrientes | +0.362 | +28% | -21.83M | SusoGattuso +27% |
| steal Cesar Tarrega from SusoGattuso · sell Beñat Turrientes | +0.311 | +25% | -7.75M | SusoGattuso +25% |
| steal Gorka Guruzeta from Magic Mike 333 · sell Beñat Turrientes | +0.301 | +23% | -14.23M | SusoGattuso +22% |
| steal Aitor Paredes from BurtonGM89 · sell Beñat Turrientes | +0.261 | +18% | +1.52M | SusoGattuso +17% |
| steal Ferran Jutgla from Albert Laporta · sell Beñat Turrientes | +0.230 | +16% | -2.43M | SusoGattuso +15% |
| steal Lucas Noubi from Magic Mike 333 · sell Beñat Turrientes | +0.171 | +12% | -11.62M | SusoGattuso +11% |
| steal Justin de Haas from BurtonGM89 · sell Beñat Turrientes | +0.159 | +10% | -7.36M | SusoGattuso +10% |

_**Δpos** is places gained on the expected finish, **Δwin** is percentage points of P(winning the league), and **net €** is what the move does to the balance — negative spends, positive raises. **Biggest gain vs** is the rival the move takes the most from, which is the column to read when one of them is the race and the rest are not._

## Sell — these never make the eleven

| Sell | Pos | Raises |
|---|---|--:|
| Dani Lorenzo | MED | 9.60M |
| Beñat Turrientes | MED | 7.07M |
| Alvaro Fernandez | POR | 4.49M |

_These start in none of the 38 remaining jornadas, so they score nothing wherever the rest of the squad goes and any offer is a gain. The simulation rates selling them at exactly zero — it cannot value the cash, which is the whole of what they are worth. What it also cannot value is cover: P(start) is held flat here, so nobody is ever injured in March and a bench that exists for that is worth nothing to it._

## Where the league stands

| Manager | now | simulated | 10–90 | P(I finish above) |
|---|--:|--:|--:|--:|
| SusoGattuso | 23 | 1,439 | 1,322–1,560 | 50% |
| miguel_autentico **(you)** | 17 | 1,437 | 1,311–1,565 | — |
| BurtonGM89 | 24 | 1,235 | 1,122–1,345 | 94% |
| Albert Laporta | 10 | 1,199 | 1,082–1,318 | 97% |
| Magic Mike 333 | 11 | 1,142 | 1,033–1,252 | 99% |

## What the simulation cannot see

_shape from the seed prior (96 observed, 200 needed)._

- **Jornada 1 is half played.** 8 clubs are done and their points are already in the `now` column, so the simulation only plays the rest of the round. It still re-picks an eleven that is in fact already locked.
- **P(start) is today's, held flat over every remaining jornada.** Nothing here knows who will be injured in March.
- **Rivals never transfer.** A steal that guts a squad assumes its manager does not simply buy someone back.
- **Teammates score independently.** Two defenders of one club share a clean sheet, so a concentrated squad really has more variance than this shows.
- **Cash scores zero.** Nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good.
