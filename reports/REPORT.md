# Liga Five Guys — one report — 2026-08-15 22:19 UTC

The four questions first, from `latest.md`. Everything else is reference and is linked, not reprinted.


**Locks in 17h** (next kickoff) · squad 229.42M · cash 63.29M · total 292.72M

## 1. Am I fielding the right eleven?

**Your XI: 4-5-1 · ≈36 pts expected next jornada** (uncalibrated — see the methodology link at the end)

| | Marked XI | vs | pts/m | Fix | FF | AF | xPts/j |
|---|---|---|--:|--:|--:|--:|--:|
| POR | Ionut Radu | Valencia A | 6.6 | = | 90% | 67% | 6.0 |
| DEF | Carl Starfelt | Valencia A | 5.1 | = | 60% | 67% | 3.1 |
| DEF | Robin Le Normand | Malaga H | 4.4 | +7% | 60% | 50% | 2.8 |
| DEF | Igor Zubeldia | Betis A | 3.7 | -11% | 60% | — | 2.0 |
| DEF | Omar El Hilali | Levante H | 3.0 | +14% | 80% | — | 2.8 |
| MED | Iñigo Ruiz de Galarreta | Sevilla H | 4.5 | +11% | 60% | — | 3.0 |
| MED | Dani Lorenzo | Atletico A | ~2.8 | -13% | 90% | 100% | 2.2 |
| MED | Pepelu | Celta Vigo H | 4.3 | +5% | 70% | 67% | 3.1 |
| MED | Jon Moncayola | Levante H | 4.4 | +14% | 90% | — | 4.5 |
| MED | Lucien Agoume | Athletic A | 3.9 | -9% | 100% | titular | 3.6 |
| DEL | Iñigo Vicente | Villarreal H | ~3.0 | -5% | 90% | 100% | 2.6 |
| +DEF | _Dean Huijsen_ | Espanyol A | 5.6 | +1% | 70% | 100% | **+1.7** |
| +DEF | _Aitor Paredes_ | Sevilla H ★ | 4.6 | +11% | 70% | 67% | **+1.0** |
| +DEF | _Eric Garcia_ | Elche A ★ | 5.5 | +8% | 50% | 100% | **+0.6** |
| +MED | _Jon Ander Olasagasti_ | Espanyol A | 3.6 | +1% | 90% | — | **+0.4** |
| +DEF | _Antonio Rudiger_ | Espanyol A | 4.8 | +1% | 50% | — | **+0.2** |

_**pts/m** is points per match, last season shrunk toward the average and blended with this season as it accrues. `~` means no record at all, so the baseline is assumed. **Fix** is how much the next opponent moves it — `=` a median team, `—` no fixture known. **FF** is futbolfantasy's start percentage, **AF** is analiticafantasy's — `titular` there is a named starter with no number attached, `?` is listed without either, `—` is not listed at all. They are separate columns because neither has been checked against a played jornada yet, so there is no weight to blend them by, and a disagreement is worth more than an average. **xPts/j** = pts/m × Fix × FF, and uses FF only. `⚠` on a name means question 2 has something on him._

_The `+SLOT` rows are today's slate: **xPts/j is the change to the whole eleven** if you sign him and re-pick the shape, not his own score, and it leaves the fixture OUT — you own a player for months, not for one round. `★` next to the opponent means this round's draw happens to be kind, `↓` that it is not; neither is in the number. What he would cost is question 4._ _6 others on the slate would not improve this eleven, so they are priced there and not here._

**The model would score ≈39 — 3.4 pts/j more.** Its shape is 4-5-1.

| Bench this | For this | Worth |
|---|---|--:|
| Dani Lorenzo (2.2) | Pablo Fornals (5.4) | +3.2 |
| Iñigo Ruiz de Galarreta (3.0) | Ruben Garcia (3.3) | +0.3 |

_Swaps are same-position only: a cross-slot difference is a change of formation, not a substitution. Your own marks are the row above — this table is advice._

## 2. Is anyone injured, suspended, or doubtful?

**Nobody in your squad is flagged.** All 16 players with an entry on their team page read as available.

_Listed with no flag (16): Alvaro Fernandez, Beñat Turrientes, Carl Starfelt, Dani Lorenzo, Igor Zubeldia, Ionut Radu, Iñigo Ruiz de Galarreta, Iñigo Vicente, Jon Moncayola, Lucien Agoume, Omar El Hilali, Pablo Fornals, Pepelu, Robin Le Normand, Ruben Garcia, Simon Eriksson._

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page. A knock the site still lists as available (`Tocado`) is folded into doubt._

## 3. Is everyone expected to start?

**The two sources disagree** — one of them has him in the eleven and the other does not. Neither has a track record here yet, so this is a prompt to open the app, not a verdict:

| | Player | The split |
|---|---|---|
| XI | Robin Le Normand | futbolfantasy 60%, analitica 50% |
| bench | Beñat Turrientes | futbolfantasy 60%, analitica 33% |

_Both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` not on the page at all (assumed 15%). Threshold is `min_start` in `inputs/league.ini`._

## 4. Anything to do in the market?

**11 on offer, 5 improve your XI, 3 cover a position you are short in.**

| Player | Pos | Value | FF | AF | XI gain | Bid | Cost/pt | Competition | Verdict |
|---|---|--:|--:|--:|--:|--:|--:|---|---|
| Gonçalo Guedes | del | 34.13M | 50% | 33% | -0.8 | 34.13M | — | none | **Cover** — XI -0.8, but you are 1 short here |
| Yeremay Hernandez | del | 51.57M | 30% | — | -1.5 | 51.57M | — | none | **Cover** — XI -1.5, but you are 1 short here |
| Ilias Akhomach | del | 5.20M | 10% | — | -1.6 | 5.33M–6.33M | — | none | **Cover** — XI -1.6, but you are 1 short here |
| Dean Huijsen | def | 31.93M | 70% | 100% | +2.0 | 31.93M | 882K | (4 broke) | **Bid** — XI +2.0 |
| Aitor Paredes | def | 5.07M | 70% | 67% | +1.6 | 5.19M–6.16M | 384K | SusoGatt +1.3, (3 broke) | **Bid** — XI +1.6 |
| Eric Garcia | def | 44.75M | 50% | 100% | +1.0 | 44.75M | 2.45M | (4 broke) | **Bid** — XI +1.0 |
| Antonio Rudiger | def | 27.74M | 50% | — | +0.5 | 28.40M–33.73M | 1.39M | SusoGatt +0.2, (3 broke) | **Bid** — XI +0.5 |
| Jon Ander Olasagasti | med | 4.33M | 90% | — | +0.2 | 4.43M–5.26M | 3.19M | SusoGatt +1.0, (3 broke) | **Bid** — XI +0.2 |
| Miguel Loureiro | def | 3.75M | 50% | — | -0.4 | 3.84M–4.57M | — | none | pass — XI -0.4 |
| Lucas Torro | med | 2.48M | 50% | — | -1.3 | 2.54M–3.02M | — | (1 broke) | pass — XI -1.3 |
| Jorge Cabello | def | 837K | 10% | — | -1.7 | 857K–1.02M | — | none | pass — XI -1.7 |

**Cost/pt** is what the marginal point actually costs, and it is not the price: a purchase is closer to a loan than a spend, because the value comes back when you sell. It is the premium you pay over the floor plus the value expected to drain away over 14 days — NOT the price, and NOT the exit. The app pays value give or take 12% on a sale, which on a large player is bigger than both other terms together; that swing is a coin flip, so it is stated here rather than averaged into a number that would look like a price. Drift is a flat mean within his price band, over readings taken before a ball was kicked — expect it to move harder once results land.

**FF** is futbolfantasy's probable-XI percentage, which is the one the forecast uses. **AF** is analiticafantasy's read of the same eleven, printed beside it and never blended in — `titular` is a named starter (a final call, with no number to it), a percentage is their editors' consensus, `?` means they list him without either, and `—` means they do not have him. Two columns that disagree are the signal; that is the whole point of carrying both.

Competition is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. The full manager-by-manager matrix is in `reports/rivals.md`.

Bid is the floor plus what this league has actually paid over it: median +2.4%, -1.2% to +21.6% (n=25). 9 of those 25 went at the floor itself, so the minimum is not a number known to lose. A dozen deals is not a distribution — the range is what has happened, not a chance of winning, and every one of them is a bid that won.

Already owned, so not a purchase: arda guler (Albert Laporta), ayoze perez (Albert Laporta), carlos espi (SusoGattuso), eder militao (BurtonGM89), ferran jutgla (Albert Laporta), ilaix moriba (Albert Laporta), marc roca (Albert Laporta), matias dituro (Albert Laporta), wojciech szczesny (SusoGattuso).

---

## Warnings

- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 unmodelled** (Iñigo Vicente) — no LaLiga record, so they carry an assumed baseline, not an earned one.

_Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger._


## Reference

Kept in full, one tap away — not reprinted here, because that is what made this file 504 lines long.

- [The rest of today's report — sell shortlist, movers](latest.md)
- [Who to buy — everyone unowned, ranked](watchlist.md)
- [Rival cash and ceilings, premiums, drift, projected XIs](rivals.md)
- [Every squad in the league, deal history, cash basis](squads.md)
- [How the forecast works, and how it's doing](methodology.md)
