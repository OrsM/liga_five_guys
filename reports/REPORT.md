# Liga Five Guys — one report — 2026-08-15 10:50 UTC

The four questions first, from `latest.md`. Everything else is reference and is linked, not reprinted.


**Locks in 6h** · squad 171.50M · cash 63.29M · total 234.79M

## 1. Am I fielding the right eleven?

**Your XI: 4-5-1 · ≈34 pts expected next jornada** (uncalibrated — see the methodology link at the end)

| | Marked XI | Start% | xPts/j | State |
|---|---|--:|--:|---|
| POR | Ionut Radu | 90% | 5.9 | fit |
| DEF | Carl Starfelt | 60% | 3.1 | fit |
| DEF | Robin Le Normand | 60% | 2.6 | fit |
| DEF | Igor Zubeldia | 80% | 3.0 | fit |
| DEF | Omar El Hilali | 80% | 2.4 | fit |
| MED | Ruben Garcia | 60% | 2.9 | fit |
| MED | Iñigo Ruiz de Galarreta | 60% | 2.7 | fit |
| MED | Dani Lorenzo | 90% | 2.6 | fit |
| MED | Jon Moncayola | 90% | 3.9 | fit |
| MED | Beñat Turrientes | 70% | 2.5 | fit |
| DEL | Iñigo Vicente | 90% | 2.7 | fit |

**The model would score ≈35 — 1.1 pts/j more.** Its shape is 4-5-1.

| Bench this | For this | Worth |
|---|---|--:|
| Beñat Turrientes (2.5) | Lucien Agoume (3.2) | +0.6 |
| Dani Lorenzo (2.6) | Pepelu (3.0) | +0.4 |

_Swaps are same-position only: a cross-slot difference is a change of formation, not a substitution. Your own marks are the row above — this table is advice._

## 2. Is anyone injured, suspended, or doubtful?

**Nobody in your squad is flagged.** All 15 players with an entry on their team page read as available.

| Player | State | What the page says |
|---|---|---|
| Alvaro Fernandez | fit | listed, no flag |
| Beñat Turrientes | fit | listed, no flag |
| Carl Starfelt | fit | listed, no flag |
| Dani Lorenzo | fit | listed, no flag |
| Igor Zubeldia | fit | listed, no flag |
| Ionut Radu | fit | listed, no flag |
| Iñigo Ruiz de Galarreta | fit | listed, no flag |
| Iñigo Vicente | fit | listed, no flag |
| Jon Moncayola | fit | listed, no flag |
| Lucien Agoume | fit | listed, no flag |
| Omar El Hilali | fit | listed, no flag |
| Pepelu | fit | listed, no flag |
| Robin Le Normand | fit | listed, no flag |
| Ruben Garcia | fit | listed, no flag |
| Simon Eriksson | fit | listed, no flag |

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page. A knock the site still lists as available (`Tocado`) is folded into doubt._

## 3. Is everyone expected to start?

**Every marked player is at 60% or above.**

| | Player | Start% | Reading |
|---|---|--:|---|
| XI | Dani Lorenzo | 90% | published |
| XI | Jon Moncayola | 90% | published |
| XI | Iñigo Vicente | 90% | published |
| XI | Ionut Radu | 90% | published |
| XI | Igor Zubeldia | 80% | published |
| XI | Omar El Hilali | 80% | published |
| XI | Beñat Turrientes | 70% | published |
| XI | Carl Starfelt | 60% | published |
| XI | Robin Le Normand | 60% | published |
| XI | Ruben Garcia | 60% | published |
| XI | Iñigo Ruiz de Galarreta | 60% | published |
| bench | Lucien Agoume | 80% | published |
| bench | Pepelu | 70% | published |
| bench | Simon Eriksson | 50% | published |
| bench | Alvaro Fernandez | 20% | published |

_`start_pct` is futbolfantasy's editorial bucket, read twice a day, not a live probability — it moved for only a handful of players across the snapshots taken so far. Threshold is `min_start` in `inputs/league.ini`._

## 4. Anything to do in the market?

**9 on offer, 2 improve your XI, 1 cover a position you are short in.**

| Player | Pos | Value | Start% | XI gain | Bid | Cost/pt | Competition | Verdict |
|---|---|--:|--:|--:|--:|--:|---|---|
| Zakaria Eddahchouri | del | 1.88M | 30% | -1.5 | 1.93M–2.29M | — | none | **Cover** — XI -1.5, but you are 1 short here |
| Pablo Fornals | med | 57.93M | 80% | +2.7 | 59.46M–63.29M | 1.18M | Magic +3.2?, (3 broke) | **Bid** — XI +2.7 |
| Santiago Mouriño | def | 40.48M | 70% | +0.7 | 41.55M–49.22M | 3.34M | Magic +0.9?, (3 broke) | **Bid** — XI +0.7 |
| Mario Martin | med | 3.83M | 80% | -0.4 | 3.93M–4.66M | — | Albert +1.0, +2 more, (1 broke) | pass — XI -0.4 |
| Pelayo Fernandez | def | 1.72M | 50% | -0.5 | 1.77M–2.09M | — | Albert +0.7, +1 more | pass — XI -0.5 |
| Clemens Riedel | def | 4.60M | 50% | -0.5 | 4.72M–5.59M | — | Albert +0.7, (1 broke) | pass — XI -0.5 |
| Oriol Rey | med | 734K | 50% | -1.0 | 754K–893K | — | Albert +0.4, +1 more | pass — XI -1.0 |
| Alex Freeman | def | 2.45M | 30% | -1.4 | 2.51M–2.97M | — | none | pass — XI -1.4 |
| Dani Vivian | def | 10.82M | 30% | -1.8 | 11.11M–13.16M | — | none | pass — XI -1.8 |

**Cost/pt** is what the marginal point actually costs, and it is not the price: a purchase is closer to a loan than a spend, because the value comes back when you sell. It is the premium you pay over the floor plus the value expected to drain away over 14 days — NOT the price, and NOT the exit. The app pays value give or take 12% on a sale, which on a large player is bigger than both other terms together; that swing is a coin flip, so it is stated here rather than averaged into a number that would look like a price. Drift is a flat mean within his price band, over readings taken before a ball was kicked — expect it to move harder once results land.

Competition is demand, not roster counts: the rivals whose XI actually improves with him, strongest threat first — `?` cash unknown (treat as live), `(n broke)` want him but cannot pay the floor. The full manager-by-manager matrix is in `reports/rivals.md`.

Bid is the floor plus what this league has actually paid over it: median +2.6%, -0.2% to +21.6% (n=21). 6 of those 21 went at the floor itself, so the minimum is not a number known to lose. A dozen deals is not a distribution — the range is what has happened, not a chance of winning, and every one of them is a bid that won.

_At least one rival's cash is an estimate, so no bid here assumes you are unopposed._

Already owned, so not a purchase: arda guler (Albert Laporta), ayoze perez (Albert Laporta), ferran jutgla (Albert Laporta), giacomo quagliata (BurtonGM89), ilaix moriba (Albert Laporta), marc roca (Albert Laporta), matias dituro (Albert Laporta), pedro bigas (Albert Laporta), quilindschy hartman (BurtonGM89), wojciech szczesny (SusoGattuso).

## Names I could not place

OCR mangled these past matching, so they are missing from the table above — re-read them off the app if one matters.

- **Bright Ede** — no match
- **Buonanotte** — no match

---

## Warnings

- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 unmodelled** (Iñigo Vicente) — no LaLiga record, so they carry an assumed baseline, not an earned one.

_Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger._

## Not in your XI (as you have marked them)

**Gap** is what the XI loses per jornada if he has to play, after re-picking the formation. **€/pt** is value per expected point: the sell shortlist, worst first.

| Player | Pos | Value | xPts/j | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Alvaro Fernandez | por | 4.73M | 0.9 | -5.1 | 5.42M | 3rd POR — only 1 can ever play |
| Pepelu | med | 7.47M | 3.0 | +0.0 | 2.48M | rising — sell into strength |
| Lucien Agoume | med | 5.82M | 3.2 | +0.0 | 1.85M | as good as the man ahead |
| Simon Eriksson | por | 2.92M | 2.2 | -3.8 | 1.34M | 2nd POR — only 1 can ever play |

_Selling to the app pays the value give or take 12%: the 13 priced sales in the ledger went median +3.3%, -9.4% to +12.0% (n=13), so read every value above as that band, not as a number. Who is short in this position, and who can still afford you, is in `reports/rivals.md`._

## Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Jon Moncayola | 6.79M | 107K | +1.61% |
| Robin Le Normand | 9.98M | 140K | +1.43% |
| Pepelu | 7.47M | 99K | +1.34% |
| Ruben Garcia | 13.75M | 173K | +1.27% |
| Beñat Turrientes | 7.06M | 78K | +1.12% |
| Ionut Radu | 39.25M | -464K | -1.17% |
| Alvaro Fernandez | 4.73M | -68K | -1.42% |

## Notes

_621 players tracked, 512 with a probable-XI reading._

_xPts/j — expected points per jornada = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Injured, suspended and unavailable score zero; a doubt is halved._

_Generated 2026-08-15 10:50 UTC._

## Rival cash

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

### Ledger warnings

- Magic Mike 333: net spend exceeds the 100M budget by 7.22M — unrecorded sales, or they started with more. Cash reported as unknown; ask before assuming they are broke.

## Reference

Kept in full, one tap away — not reprinted here, because that is what made this file 504 lines long.

- [Who to buy — everyone unowned, ranked](watchlist.md)
- [How your rivals bid — premiums, drift, projected XIs](rivals.md)
- [Every squad in the league, deal history, cash basis](squads.md)
- [How the forecast works, and how it's doing](methodology.md)
