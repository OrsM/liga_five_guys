# The simulation — 2026-08-18T0941Z

## What the simulation says to do

_One question, asked of every move you could make: if I did this, where would I finish?_

**Locks in 25h** · squad 213.11M · cash 23.60M · total 236.71M

**Expected finish 2.14** · **P(win) 11%** · season **1,480–1,738** (10–90)

_38 jornadas left · 41 players acquirable · 80 moves simulated._

| Get | Give up | P(win) | Net € | Left |
|---|---|--:|--:|--:|
| Dean Huijsen ← BurtonGM89 | Turrientes + Lorenzo | 34% | -18.42M | 5.17M |
| Ferran Jutgla ← Albert Laporta | Turrientes | 21% | -2.43M | 21.16M |
| Marcos Alonso (free) | Turrientes | 18% | -21.83M | 1.77M |
| Aitor Paredes ← BurtonGM89 | Turrientes | 18% | +1.52M | 25.12M |
| Marc Bartra (free) | Turrientes | 15% | -20.68M | 2.92M |
| Carlos Puga ← BurtonGM89 | Turrientes | 11% | +1.91M | 25.50M |
| Marc Roca ← Albert Laporta | Turrientes | 11% | +1.67M | 25.27M |
| Andriy Lunin (free) | Turrientes | 11% | +4.96M | 28.56M |

_**P(win)** is where the move LEAVES you — your chance of winning the league after making it, against 11% if you do nothing. **Get** names the rival a steal takes him off, which is half of what a steal is worth: it raises your total and lowers theirs at once. **Net €** is what the move does to the balance and **Left** is what you are on afterwards — every rival is on 0K until you pay one, so that column is the whole of your ability to answer anything for the rest of the season. Who exactly you give up when it says *spares* is in the sell table below; none of them ever start. **he takes** is the rival's best answer, played before the season is._

_A CLAUSE IS THREE PURCHASES AT ONCE. The market value buys the points for you, and that part is a loan rather than a spend — it comes back when you sell him. The premium over it buys something else entirely: that a RIVAL does not score them. And the balance buys nothing at all, it only stops being available. The first is priced by the market; the second is now scored net of his reply, because he is handed the money and spends it; the third is the column on the right, because nothing here can value it._

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
