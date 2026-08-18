# Liga Five Guys — one report — 2026-08-18 15:01 UTC

Every move you could make, ranked by whether it wins the league. Everything else is reference and is linked, not reprinted.


## What the simulation says to do

_One question, asked of every move you could make: if I did this, where would I finish?_

**Locks in 28h** · squad 213.11M · cash 23.60M · total 236.71M

**Expected finish 1.61** · **P(win) 49%** · season **1,311–1,565** (10–90)

_38 jornadas left · 83 players acquirable · 137 moves simulated._

| Get | Give up | P(win) | Net € |
|---|---|--:|--:|
| Giuliano Simeone ← SusoGattuso | 3 spares | 90% | -23.49M |
| Yuri Berchiche ← SusoGattuso | Turrientes | 84% | -14.13M |
| Leo Roman ← SusoGattuso | Turrientes + Lorenzo | 83% | -20.86M |
| Marcos Alonso (free) | Turrientes | 77% | -21.83M |
| Cesar Tarrega ← SusoGattuso | Turrientes | 74% | -7.75M |
| Gorka Guruzeta ← Magic Mike 333 | Turrientes | 71% | -14.23M |
| Dean Huijsen ← BurtonGM89 | Turrientes + Lorenzo | 70% | -18.42M |
| Aitor Paredes ← BurtonGM89 | Turrientes | 67% | +1.52M |

_**P(win)** is where the move LEAVES you — your chance of winning the league after making it, against 49% if you do nothing. **Get** names the rival a steal takes him off, which is half of what a steal is worth: it raises your total and lowers theirs at once. **Net €** is what the move does to the balance — negative spends, positive raises. Who exactly you give up when it says *spares* is in the sell table below; none of them ever start._

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


_What the one table in [REPORT.md](REPORT.md) was built from: the eleven it assumes you field, what a man on today's slate should cost, and the two ways any of it can be wrong about a player._

## Warnings

- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 unmodelled** (Iñigo Vicente) — no LaLiga record, so they carry an assumed baseline, not an earned one.

_Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger._


## Reference

Kept in full, one tap away — not reprinted here, because that is what made this file 504 lines long.

- [The workings — the eleven, bids, the basket, sales, movers](latest.md)
- [Every squad, cash, what each manager pays over value](league.md)
- [How the forecast works, and how it's doing](methodology.md)
