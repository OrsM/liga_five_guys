# Liga Five Guys — one report — 2026-08-18 18:31 UTC

Every move you could make, ranked by whether it wins the league. Everything else is reference and is linked, not reprinted.


## What the simulation says to do

_One question, asked of every move you could make: if I did this, where would I finish?_

**Locks in 25h** · squad 213.11M · cash 23.60M · total 236.71M

**Expected finish 2.14** · **P(win) 11%** · season **1,480–1,738** (10–90)

_38 jornadas left · 41 players acquirable · 52 moves simulated._

| Get | Give up | P(win) | Net € | Left |
|---|---|--:|--:|--:|
| Dean Huijsen ← BurtonGM89 | Turrientes + Lorenzo | 34% | -18.42M | 5.17M |
| Ferran Jutgla ← Albert Laporta | Lorenzo | 21% | +98K | 23.69M |
| Nahuel Tenaglia ← BurtonGM89 | Turrientes | 20% | -9.92M | 13.68M |
| Marcos Alonso (free) | Turrientes | 18% | -21.83M | 1.77M |
| Aitor Paredes ← BurtonGM89 | Turrientes | 18% | +1.52M | 25.12M |
| Santiago Mouriño ← Albert Laporta | Turrientes + Lorenzo | 17% | -23.54M | 55K |
| Antonio Blanco ← BurtonGM89 | Turrientes | 16% | -7.82M | 15.78M |
| Marc Bartra (free) | Lorenzo | 15% | -18.15M | 5.45M |

_**P(win)** is where the move LEAVES you — your chance of winning the league after making it, against 11% if you do nothing. **Get** names the rival a steal takes him off, which is half of what a steal is worth: it raises your total and lowers theirs at once. **Net €** is what the move does to the balance and **Left** is what you are on afterwards — every rival is on 0K until you pay one, so that column is the whole of your ability to answer anything for the rest of the season. Who exactly you give up when it says *spares* is in the sell table below; none of them ever start. **he takes** is the rival's best answer, played before the season is._

_A CLAUSE IS THREE PURCHASES AT ONCE. The market value buys the points for you, and that part is a loan rather than a spend — it comes back when you sell him. The premium over it buys something else entirely: that a RIVAL does not score them. And the balance buys nothing at all, it only stops being available. The first is priced by the market; the second is now scored net of his reply, because he is handed the money and spends it; the third is the column on the right, because nothing here can value it._

## Or wait

| Do | What opens | When | best upgrade then vs now |
|---|---|--:|--:|
| Wait | 62 players | 6 days | +4.62 vs +3.69 |

_**62 rival players have a locked clause** and open on 24 Aug, in about 6 days. The best of them is worth +4.62 xPts/j against your eleven; the best you can buy today is +3.69. Waiting scores ZERO in the table above — not because it is worthless but because nothing here models a market you have not seen yet, so every move with a positive number beats it by construction. The balance in the **Left** column is what buys the choice._

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
| SusoGattuso | 23 | 1,782 | 1,656–1,910 | 11% |
| miguel_autentico **(you)** | 17 | 1,609 | 1,480–1,738 | — |
| BurtonGM89 | 24 | 1,470 | 1,354–1,587 | 85% |
| Magic Mike 333 | 11 | 1,431 | 1,313–1,553 | 91% |
| Albert Laporta | 10 | 1,245 | 1,133–1,361 | 100% |

## What the simulation cannot see

_shape from the seed prior (96 observed, 200 needed)._

_P(start) fitted on 240 confirmed starts across 8 team sheets: futbolfantasy recalibrated (logit -0.5 +5.8x), blended 80% with analiticafantasy where it has an opinion (a named starter counts 94%). Brier improves 0.032 on line-ups the fit had not seen._

- **Jornada 1 is half played.** 8 clubs are done and their points are already in the `now` column, so the simulation only plays the rest of the round. It still re-picks an eleven that is in fact already locked.
- **A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000.** A clause runs a median 1.52x market value in this league and the app only ever pays the value back, so the premium is gone for good. It is charged against the move rather than ignored — but the price is measured off what more money would actually buy you today, and on most days that is very little.
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
